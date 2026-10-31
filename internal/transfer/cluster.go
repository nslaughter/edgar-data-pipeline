package transfer

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/portforward"
	"k8s.io/client-go/transport/spdy"
	streamhttp "k8s.io/streaming/pkg/httpstream"
)

// The names the umbrella chart gives what the transfer reads, which
// tests/test_deployment.py checks it renders. The transfer and the chart
// agree on them, as spec/deployment.md leaves them to (section 5, the
// transfer).
const (
	// Namespace is the release namespace (TRANSFER-3).
	Namespace = "edgar"
	// Cluster is the CloudNativePG Cluster, whose primary's Service is its
	// name followed by -rw (TRANSFER-5).
	Cluster = "postgres"
	// Database is the pipeline's database, and Role the transfer's role
	// in it (PG-15).
	Database = "edgar"
	Role     = "transfer"
	// PasswordSecret holds the role's password, and KeySecret the
	// transfer's Garage key, with the region of Garage's S3 API
	// (TRANSFER-1).
	PasswordSecret = "postgres-transfer"
	KeySecret      = "garage-key-transfer"
	// S3Service is Garage's Service, whose port S3Port is its S3 API, and
	// DatabasePort is the database's port (TRANSFER-5).
	S3Service    = "garage"
	S3Port       = 3900
	DatabasePort = 5432
	// Bucket is the cluster store's bucket (GARAGE-4).
	Bucket = "snapshots"
	// FakeEDGAR is the fake EDGAR's Deployment, which only CI installs
	// (FAKE-2, TRANSFER-6).
	FakeEDGAR = "fake-edgar"
)

// ErrNoContext says that the owner named no context, so the transfer reads
// nothing (TRANSFER-3).
var ErrNoContext = errors.New("name the context of the cluster to read with --context: " +
	"the transfer reads no cluster without one, the kubeconfig's current context included")

// RESTConfig gives the configuration of the API server of the context named,
// with the owner's credentials for it, from their kubeconfig: the file
// given, or else those KUBECONFIG lists, or else ~/.kube/config, as kubectl
// reads them. It never takes the kubeconfig's current context in place of
// one named (TRANSFER-3, TRANSFER-4).
func RESTConfig(kubeconfig, name string) (*rest.Config, error) {
	if name == "" {
		return nil, ErrNoContext
	}
	rules := clientcmd.NewDefaultClientConfigLoadingRules()
	rules.ExplicitPath = kubeconfig
	loader := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(rules,
		&clientcmd.ConfigOverrides{CurrentContext: name})
	raw, err := loader.RawConfig()
	if err != nil {
		return nil, err
	}
	if _, ok := raw.Contexts[name]; !ok {
		return nil, fmt.Errorf("the kubeconfig has no context %q", name)
	}
	return loader.ClientConfig()
}

// Kube is the API server of the cluster the owner named, reached with their
// credentials for it.
type Kube struct {
	Config *rest.Config
	Client kubernetes.Interface
}

// NewKube gives the API server the configuration names.
func NewKube(config *rest.Config) (Kube, error) {
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return Kube{}, err
	}
	return Kube{Config: config, Client: client}, nil
}

// FakeEDGAR says whether the release namespace holds the fake EDGAR's
// Deployment (TRANSFER-6).
func (k Kube) FakeEDGAR(ctx context.Context) (bool, error) {
	_, err := k.Client.AppsV1().Deployments(Namespace).Get(ctx, FakeEDGAR, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("looking for the fake EDGAR's Deployment: %w", err)
	}
	return true, nil
}

// Credentials are the transfer's own for the cluster store: its role's
// password, and its key's ID and secret, with the region of Garage's S3 API
// (TRANSFER-1). They print as their name, never their values, so no error
// or log shows them (TRANSFER-4).
type Credentials struct {
	Password         string
	KeyID, KeySecret string
	Region           string
}

// String gives the credentials' name.
func (Credentials) String() string { return "the transfer's credentials" }

// GoString gives the credentials' name, as %#v prints them.
func (Credentials) GoString() string { return "the transfer's credentials" }

// Credentials reads the transfer's credentials from its two Secrets, and
// reads no other Secret (TRANSFER-4).
func (k Kube) Credentials(ctx context.Context) (Credentials, error) {
	password, err := k.secret(ctx, PasswordSecret, "password")
	if err != nil {
		return Credentials{}, err
	}
	key, err := k.secret(ctx, KeySecret, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")
	if err != nil {
		return Credentials{}, err
	}
	return Credentials{Password: password[0], KeyID: key[0], KeySecret: key[1], Region: key[2]}, nil
}

func (k Kube) secret(ctx context.Context, name string, keys ...string) ([]string, error) {
	secret, err := k.Client.CoreV1().Secrets(Namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("reading the Secret %s: %w", name, err)
	}
	values := make([]string, len(keys))
	for i, key := range keys {
		value, ok := secret.Data[key]
		if !ok || len(value) == 0 {
			return nil, fmt.Errorf("the Secret %s has no %s", name, key)
		}
		values[i] = string(value)
	}
	return values, nil
}

// Forward is a port-forward through the API server to a pod of a Service,
// as kubectl port-forward opens one, listening on a port of the loopback
// address (TRANSFER-5).
type Forward struct {
	// Port is the local port that reaches the pod.
	Port uint16
	stop chan struct{}
	done chan error
	once sync.Once
}

// Forward opens a port-forward to the port of a Service in the release
// namespace: to the port its pod's container serves it on, of a ready pod
// that the Service selects.
func (k Kube) Forward(ctx context.Context, service string, port int32) (*Forward, error) {
	pod, target, err := k.target(ctx, service, port)
	if err != nil {
		return nil, err
	}
	url := k.Client.CoreV1().RESTClient().Post().
		Resource("pods").Namespace(Namespace).Name(pod).SubResource("portforward").URL()
	transport, upgrader, err := spdy.RoundTripperFor(k.Config)
	if err != nil {
		return nil, err
	}
	dialer := spdy.NewDialerForStreaming(upgrader, &http.Client{Transport: transport}, http.MethodPost, url)
	tunneling, err := portforward.NewSPDYOverWebsocketDialerForStreaming(url, k.Config)
	if err != nil {
		return nil, err
	}
	// kubectl's dialers: over a WebSocket, or SPDY where the API server
	// doesn't upgrade to one.
	dialer = portforward.NewFallbackDialerForStreaming(tunneling, dialer, func(err error) bool {
		return streamhttp.IsUpgradeFailure(err) || streamhttp.IsHTTPSProxyError(err)
	})
	dialer = cancellable{ctx: ctx, dialer: dialer}
	f := &Forward{stop: make(chan struct{}), done: make(chan error, 1)}
	ready := make(chan struct{})
	forwarder, err := portforward.NewOnAddressesForStreaming(dialer, []string{"127.0.0.1"},
		[]string{fmt.Sprintf("0:%d", target)}, f.stop, ready, io.Discard, io.Discard)
	if err != nil {
		return nil, err
	}
	go func() { f.done <- forwarder.ForwardPorts() }()
	select {
	case <-ready:
	case err := <-f.done:
		return nil, fmt.Errorf("forwarding a port to %s: %w", service, err)
	case <-ctx.Done():
		_ = f.Close()
		return nil, ctx.Err()
	}
	ports, err := forwarder.GetPorts()
	if err != nil {
		_ = f.Close()
		return nil, err
	}
	f.Port = ports[0].Local
	return f, nil
}

// cancellable is a dialer that gives up once its context is done. client-go's
// dialers send their upgrade requests without a context, and wait for the
// API server's answer however long it takes, so a handshake that stalls
// would hold the transfer, and Close, even once the owner interrupts it. The
// handshake it gives up on goes on alone, and the connection it makes, if
// any, is closed.
type cancellable struct {
	ctx    context.Context
	dialer streamhttp.Dialer
}

func (d cancellable) Dial(protocols ...string) (streamhttp.Connection, string, error) {
	type dialed struct {
		conn     streamhttp.Connection
		protocol string
		err      error
	}
	result := make(chan dialed, 1)
	go func() {
		conn, protocol, err := d.dialer.Dial(protocols...)
		result <- dialed{conn, protocol, err}
	}()
	select {
	case r := <-result:
		return r.conn, r.protocol, r.err
	case <-d.ctx.Done():
		go func() {
			if r := <-result; r.conn != nil {
				_ = r.conn.Close()
			}
		}()
		return nil, "", d.ctx.Err()
	}
}

// Close closes the port-forward, waits until it has, and gives the error
// that ended it, if any.
func (f *Forward) Close() error {
	var err error
	f.once.Do(func() {
		close(f.stop)
		err = <-f.done
	})
	return err
}

// target gives a ready pod the Service selects, and the port its container
// serves the Service's port on, as kubectl port-forward resolves a Service.
func (k Kube) target(ctx context.Context, service string, port int32) (string, int32, error) {
	svc, err := k.Client.CoreV1().Services(Namespace).Get(ctx, service, metav1.GetOptions{})
	if err != nil {
		return "", 0, fmt.Errorf("reading the Service %s: %w", service, err)
	}
	if len(svc.Spec.Selector) == 0 {
		return "", 0, fmt.Errorf("the Service %s selects no pods", service)
	}
	var servicePort *corev1.ServicePort
	for i := range svc.Spec.Ports {
		if svc.Spec.Ports[i].Port == port {
			servicePort = &svc.Spec.Ports[i]
		}
	}
	if servicePort == nil {
		return "", 0, fmt.Errorf("the Service %s has no port %d", service, port)
	}
	pods, err := k.Client.CoreV1().Pods(Namespace).List(ctx, metav1.ListOptions{
		LabelSelector: labels.SelectorFromSet(svc.Spec.Selector).String(),
	})
	if err != nil {
		return "", 0, fmt.Errorf("listing the pods of the Service %s: %w", service, err)
	}
	sort.Slice(pods.Items, func(i, j int) bool { return pods.Items[i].Name < pods.Items[j].Name })
	for i := range pods.Items {
		pod := &pods.Items[i]
		if !ready(pod) {
			continue
		}
		target, err := containerPort(pod, servicePort)
		if err != nil {
			return "", 0, fmt.Errorf("the Service %s's port %d: %w", service, port, err)
		}
		return pod.Name, target, nil
	}
	return "", 0, fmt.Errorf("no pod of the Service %s is ready", service)
}

func ready(pod *corev1.Pod) bool {
	if pod.DeletionTimestamp != nil || pod.Status.Phase != corev1.PodRunning {
		return false
	}
	for _, condition := range pod.Status.Conditions {
		if condition.Type == corev1.PodReady {
			return condition.Status == corev1.ConditionTrue
		}
	}
	return false
}

// containerPort gives the port a pod serves a Service's port on: its target
// port, by number or by the name of a container's port, or else the
// Service's port itself.
func containerPort(pod *corev1.Pod, port *corev1.ServicePort) (int32, error) {
	switch {
	case port.TargetPort.Type == intstr.String:
		for _, container := range pod.Spec.Containers {
			for _, p := range container.Ports {
				if p.Name == port.TargetPort.StrVal {
					return p.ContainerPort, nil
				}
			}
		}
		return 0, fmt.Errorf("the pod %s has no port %s", pod.Name, port.TargetPort.StrVal)
	case port.TargetPort.IntVal != 0:
		return port.TargetPort.IntVal, nil
	default:
		return port.Port, nil
	}
}

// primaryService is the Service CloudNativePG makes for the Cluster's
// primary (TRANSFER-5).
func primaryService() string {
	return Cluster + "-rw"
}
