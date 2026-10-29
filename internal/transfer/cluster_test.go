package transfer

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

// kubeconfig writes a kubeconfig whose current context is "current", with
// another context, "named", each of a cluster of its own.
func kubeconfig(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config")
	write(t, path, `apiVersion: v1
kind: Config
current-context: current
clusters:
- name: current
  cluster: {server: "https://current.invalid:6443"}
- name: named
  cluster: {server: "https://named.invalid:6443"}
users:
- name: owner
  user: {token: "a token of the owner's"}
contexts:
- name: current
  context: {cluster: current, user: owner}
- name: named
  context: {cluster: named, user: owner}
`)
	return path
}

// spec: TRANSFER-3
func TestTheTransferActsOnTheClusterOfTheContextNamedNotTheCurrentOne(t *testing.T) {
	path := kubeconfig(t)

	config, err := RESTConfig(path, "named")

	if err != nil {
		t.Fatal(err)
	}
	if config.Host != "https://named.invalid:6443" {
		t.Errorf("host: %s", config.Host)
	}
}

// spec: TRANSFER-3
func TestWithoutAContextOrWithOneTheKubeconfigLacksTheTransferReachesNoCluster(t *testing.T) {
	path := kubeconfig(t)
	t.Setenv("KUBECONFIG", path)

	if _, err := RESTConfig("", ""); !errors.Is(err, ErrNoContext) {
		t.Errorf("without a context: %v", err)
	}
	if _, err := RESTConfig(path, ""); !errors.Is(err, ErrNoContext) {
		t.Errorf("without a context, with a kubeconfig: %v", err)
	}
	if _, err := RESTConfig(path, "absent"); err == nil || !strings.Contains(err.Error(), `no context "absent"`) {
		t.Errorf("with a context the kubeconfig lacks: %v", err)
	}
}

// spec: TRANSFER-4
func TestTheTransferReachesTheAPIServerWithTheOwnersCredentialsFromTheirKubeconfig(t *testing.T) {
	config, err := RESTConfig(kubeconfig(t), "named")

	if err != nil {
		t.Fatal(err)
	}
	if config.BearerToken != "a token of the owner's" {
		t.Errorf("the kubeconfig's credentials weren't used")
	}
}

// The transfer's credentials as the tests' Secrets hold them.
var testCredentials = Credentials{
	Password: "the-transfers-password-0f3a", KeyID: "GK0123456789abcdef01234567",
	KeySecret: "the-transfers-key-secret-9b1e", Region: "garage",
}

func secret(name string, data map[string]string) *corev1.Secret {
	s := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: Namespace}, Data: map[string][]byte{}}
	for k, v := range data {
		s.Data[k] = []byte(v)
	}
	return s
}

func cluster(objects ...runtime.Object) (Kube, *fake.Clientset) {
	client := fake.NewClientset(objects...)
	return Kube{Client: client}, client
}

// spec: TRANSFER-4
func TestTheTransferReadsItsCredentialsFromItsTwoSecretsAndNoOther(t *testing.T) {
	kube, client := cluster(
		secret(PasswordSecret, map[string]string{"username": Role, "password": testCredentials.Password}),
		secret(KeySecret, map[string]string{
			"AWS_ACCESS_KEY_ID": testCredentials.KeyID, "AWS_SECRET_ACCESS_KEY": testCredentials.KeySecret,
			"AWS_REGION": testCredentials.Region,
		}),
		secret("postgres-fetcher", map[string]string{"username": "fetcher", "password": "the fetcher's"}),
		secret("garage-key-fetcher", map[string]string{"AWS_ACCESS_KEY_ID": "the fetcher's"}),
	)

	creds, err := kube.Credentials(context.Background())

	if err != nil {
		t.Fatal(err)
	}
	if creds != testCredentials {
		t.Errorf("the credentials aren't the Secrets'")
	}
	var read []string
	for _, action := range client.Actions() {
		if action.GetResource().Resource != "secrets" {
			continue
		}
		get, ok := action.(k8stesting.GetAction)
		if !ok {
			t.Errorf("a %s of Secrets", action.GetVerb())
			continue
		}
		read = append(read, get.GetNamespace()+"/"+get.GetName())
	}
	if want := []string{"edgar/" + PasswordSecret, "edgar/" + KeySecret}; fmt.Sprint(read) != fmt.Sprint(want) {
		t.Errorf("Secrets read: %v", read)
	}
}

// spec: TRANSFER-4
func TestTheCredentialsNeverPrintTheirValues(t *testing.T) {
	for _, form := range []string{"%v", "%+v", "%#v", "%s"} {
		printed := fmt.Sprintf(form, testCredentials)
		for _, value := range []string{testCredentials.Password, testCredentials.KeyID, testCredentials.KeySecret} {
			if strings.Contains(printed, value) {
				t.Errorf("%s prints a credential: %s", form, printed)
			}
		}
	}
}

func TestASecretWithoutItsKeyIsAnErrorThatNamesTheKeyNotAValue(t *testing.T) {
	kube, _ := cluster(
		secret(PasswordSecret, map[string]string{"password": testCredentials.Password}),
		secret(KeySecret, map[string]string{"AWS_ACCESS_KEY_ID": testCredentials.KeyID}),
	)

	_, err := kube.Credentials(context.Background())

	if err == nil || !strings.Contains(err.Error(), "AWS_SECRET_ACCESS_KEY") ||
		strings.Contains(err.Error(), testCredentials.KeyID) {
		t.Errorf("error: %v", err)
	}
}

// spec: TRANSFER-4, TRANSFER-5
func TestTheDatabaseConnectionTakesNothingFromLibpqsEnvironmentOrItsFiles(t *testing.T) {
	passfile := filepath.Join(t.TempDir(), "pgpass")
	write(t, passfile, "*:*:*:*:a password from a file\n")
	if err := os.Chmod(passfile, 0o600); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{
		"PGHOST": "elsewhere.invalid", "PGPORT": "6543", "PGDATABASE": "other", "PGUSER": "edgar",
		"PGPASSWORD": "a password from the environment", "PGPASSFILE": passfile, "PGSSLMODE": "disable",
		"PGCHANNELBINDING": "disable", "PGSSLROOTCERT": filepath.Join(t.TempDir(), "absent.crt"),
		"PGAPPNAME": "other", "PGOPTIONS": "-c default_transaction_read_only=off",
		"PGTARGETSESSIONATTRS": "standby",
	} {
		t.Setenv(name, value)
	}

	config, err := databaseConfig(15432, testCredentials.Password)

	if err != nil {
		t.Fatal(err)
	}
	got := fmt.Sprintf("%s:%d %s %s", config.Host, config.Port, config.Database, config.User)
	if got != "127.0.0.1:15432 edgar transfer" {
		t.Errorf("connects to %s", got)
	}
	if config.Password != testCredentials.Password {
		t.Errorf("the password isn't the Secret's")
	}
	if config.TLSConfig == nil || len(config.Fallbacks) != 0 || config.ChannelBinding != "require" {
		t.Errorf("TLS %v, %d fallbacks, channel binding %q", config.TLSConfig != nil, len(config.Fallbacks),
			config.ChannelBinding)
	}
	if fmt.Sprint(config.RuntimeParams) != "map[application_name:transfer]" || config.ValidateConnect != nil {
		t.Errorf("parameters %v", config.RuntimeParams)
	}
}

// spec: TRANSFER-5
func TestTheDatabaseConnectionRequiresTLSAndChannelBinding(t *testing.T) {
	config, err := databaseConfig(15432, testCredentials.Password)
	if err != nil {
		t.Fatal(err)
	}

	if config.TLSConfig == nil || !config.TLSConfig.InsecureSkipVerify {
		t.Errorf("TLS: %+v", config.TLSConfig)
	}
	if config.ChannelBinding != "require" || len(config.Fallbacks) != 0 {
		t.Errorf("channel binding %q, %d fallbacks", config.ChannelBinding, len(config.Fallbacks))
	}
}

// spec: TRANSFER-4, TRANSFER-5
func TestTheS3ClientUsesTheTransfersKeyAndRegionAndNoneFromTheEnvironment(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "GKfromtheenvironment")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "a secret from the environment")
	t.Setenv("AWS_REGION", "us-east-1")
	t.Setenv("AWS_PROFILE", "other")
	t.Setenv("AWS_ENDPOINT_URL", "https://elsewhere.invalid")

	client := s3Client("http://127.0.0.1:13900", testCredentials)

	options := client.Options()
	creds, err := options.Credentials.Retrieve(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if creds.AccessKeyID != testCredentials.KeyID || creds.SecretAccessKey != testCredentials.KeySecret {
		t.Errorf("the key isn't the Secret's")
	}
	if options.Region != "garage" || *options.BaseEndpoint != "http://127.0.0.1:13900" || !options.UsePathStyle {
		t.Errorf("region %s, endpoint %s", options.Region, *options.BaseEndpoint)
	}
}

func pod(name string, ready bool, labels map[string]string, ports ...corev1.ContainerPort) *corev1.Pod {
	status := corev1.ConditionFalse
	if ready {
		status = corev1.ConditionTrue
	}
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: Namespace, Labels: labels},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "main", Ports: ports}}},
		Status: corev1.PodStatus{
			Phase:      corev1.PodRunning,
			Conditions: []corev1.PodCondition{{Type: corev1.PodReady, Status: status}},
		},
	}
}

func service(name string, selector map[string]string, port int32, target intstr.IntOrString) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: Namespace},
		Spec: corev1.ServiceSpec{
			Selector: selector,
			Ports:    []corev1.ServicePort{{Port: port, TargetPort: target}},
		},
	}
}

// spec: TRANSFER-5
func TestAPortForwardReachesAReadyPodOfTheServiceOnItsContainersPort(t *testing.T) {
	primary := map[string]string{"cnpg.io/cluster": Cluster, "cnpg.io/instanceRole": "primary"}
	garage := map[string]string{"app.kubernetes.io/name": "garage"}
	postgresql := corev1.ContainerPort{Name: "postgresql", ContainerPort: 5432}
	kube, _ := cluster(
		service(primaryService(), primary, DatabasePort, intstr.FromString("postgresql")),
		pod("postgres-1", false, primary, postgresql),
		pod("postgres-2", true, primary, postgresql),
		pod("postgres-3", true, map[string]string{"cnpg.io/cluster": Cluster, "cnpg.io/instanceRole": "replica"},
			postgresql),
		service(S3Service, garage, S3Port, intstr.FromInt32(3900)),
		pod("garage-0", true, garage),
	)

	for _, c := range []struct {
		service string
		port    int32
		want    string
	}{
		{"postgres-rw", DatabasePort, "postgres-2:5432"},
		{"garage", S3Port, "garage-0:3900"},
	} {
		name, port, err := kube.target(context.Background(), c.service, c.port)
		if err != nil {
			t.Fatal(err)
		}
		if got := fmt.Sprintf("%s:%d", name, port); got != c.want {
			t.Errorf("%s: %s", c.service, got)
		}
	}
	if _, _, err := kube.target(context.Background(), "garage", 3903); err == nil {
		t.Errorf("forwarded a port the Service lacks")
	}
}

// spec: TRANSFER-6
func TestTheFakeEDGARIsFoundByItsDeploymentInTheReleaseNamespace(t *testing.T) {
	deployment := func(namespace string) *appsv1.Deployment {
		return &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: FakeEDGAR, Namespace: namespace}}
	}
	for _, c := range []struct {
		objects []runtime.Object
		want    bool
	}{
		{nil, false},
		{[]runtime.Object{deployment("other")}, false},
		{[]runtime.Object{deployment(Namespace)}, true},
	} {
		kube, _ := cluster(c.objects...)

		found, err := kube.FakeEDGAR(context.Background())

		if err != nil || found != c.want {
			t.Errorf("%d objects: found %v, %v", len(c.objects), found, err)
		}
	}
}
