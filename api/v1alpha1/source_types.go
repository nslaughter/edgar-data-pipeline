package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// SourceSpec is a Source's spec, which the owner sets through the chart's
// values (spec/controls.md, section 6).
type SourceSpec struct {
	// Feed is the feed its polls are recorded under, such as submissions.
	// +kubebuilder:validation:Pattern=`^[a-z0-9][a-z0-9-]*$`
	Feed string `json:"feed"`

	// URL is the URL polled.
	// +kubebuilder:validation:Pattern=`^https://(www|data)\.sec\.gov/`
	URL string `json:"url"`

	// CadenceSeconds is the seconds from one poll to the next.
	// +kubebuilder:validation:Minimum=1
	CadenceSeconds int32 `json:"cadenceSeconds"`

	// Paused says whether its polls are paused.
	// +optional
	// +kubebuilder:default=false
	Paused bool `json:"paused,omitempty"`
}

// SourceStatus is a Source's status, which the scheduler sets.
type SourceStatus struct {
	// LastPollTime is when the last poll whose record is written was sent:
	// its record's requested_at.
	// +optional
	LastPollTime Timestamp `json:"lastPollTime,omitempty"`

	// LastSuccessfulPollTime is when the last poll that succeeded was sent.
	// +optional
	LastSuccessfulPollTime Timestamp `json:"lastSuccessfulPollTime,omitempty"`

	// OpenGapSince is when the open gap the record holds for the Source
	// began, present only while there is one.
	// +optional
	OpenGapSince Timestamp `json:"openGapSince,omitempty"`

	// Conditions are Polling and WithinBudget.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// Source is one URL the pipeline polls, such as one company's submissions.
//
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Feed",type=string,JSONPath=`.spec.feed`
// +kubebuilder:printcolumn:name="Cadence",type=integer,JSONPath=`.spec.cadenceSeconds`
// +kubebuilder:printcolumn:name="Paused",type=boolean,JSONPath=`.spec.paused`
// +kubebuilder:printcolumn:name="Polling",type=string,JSONPath=`.status.conditions[?(@.type=="Polling")].status`
// +kubebuilder:printcolumn:name="Succeeded",type=date,JSONPath=`.status.lastSuccessfulPollTime`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
type Source struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec SourceSpec `json:"spec"`
	// +optional
	Status SourceStatus `json:"status,omitempty"`
}

// SourceList is a list of Sources.
//
// +kubebuilder:object:root=true
type SourceList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Source `json:"items"`
}

// The conditions of a Source, and their reasons (spec/controls.md, section 6).
const (
	ConditionPolling      = "Polling"
	ConditionWithinBudget = "WithinBudget"

	ReasonPolling         = "Polling"
	ReasonNoSecAccess     = "NoSecAccess"
	ReasonRequestsStopped = "RequestsStopped"
	ReasonRequestsPaused  = "RequestsPaused"
	ReasonPaused          = "Paused"
	ReasonWithinBudget    = "WithinBudget"
	ReasonOverBudget      = "OverBudget"
)
