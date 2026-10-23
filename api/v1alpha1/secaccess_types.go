package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// SecAccessName is the name of the one SecAccess a cluster has.
const SecAccessName = "cluster"

// SecAccessSpec is the SecAccess's spec (spec/controls.md, section 8).
type SecAccessSpec struct {
	// Paused says whether every request to the SEC is paused.
	// +optional
	// +kubebuilder:default=false
	Paused bool `json:"paused,omitempty"`

	// AcknowledgedStopAt is the at of the stop the owner acknowledges.
	// +optional
	AcknowledgedStopAt Timestamp `json:"acknowledgedStopAt,omitempty"`
}

// SecAccessStatus is the SecAccess's status, which the fetcher sets.
type SecAccessStatus struct {
	// Stop is the last stop: the fetch that got a 403.
	// +optional
	Stop *Stop `json:"stop,omitempty"`
}

// Stop is the fetch that got a 403.
type Stop struct {
	// At is when that fetch's request was sent: its record's requested_at.
	At Timestamp `json:"at"`

	// URL is that fetch's URL.
	URL string `json:"url"`
}

// SecAccess says whether the pipeline may send requests to the SEC at all:
// one per cluster, named cluster.
//
// +kubebuilder:object:root=true
// +kubebuilder:resource:path=secaccesses,scope=Cluster
// +kubebuilder:subresource:status
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'cluster'",message="the SecAccess must be named cluster"
// +kubebuilder:printcolumn:name="Paused",type=boolean,JSONPath=`.spec.paused`
// +kubebuilder:printcolumn:name="Stopped",type=string,JSONPath=`.status.stop.at`
// +kubebuilder:printcolumn:name="Acknowledged",type=string,JSONPath=`.spec.acknowledgedStopAt`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
type SecAccess struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec SecAccessSpec `json:"spec"`
	// +optional
	Status SecAccessStatus `json:"status,omitempty"`
}

// SecAccessList is a list of SecAccesses.
//
// +kubebuilder:object:root=true
type SecAccessList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []SecAccess `json:"items"`
}

// Stopped says whether the SecAccess has a stop the owner hasn't
// acknowledged: one whose at isn't the acknowledgedStopAt.
func (s *SecAccess) Stopped() bool {
	return s.Status.Stop != nil && s.Status.Stop.At != s.Spec.AcknowledgedStopAt
}
