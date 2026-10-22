package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// BackfillSpec is a Backfill's spec, which can't change once the Backfill
// is created (spec/controls.md, section 7).
//
// +kubebuilder:validation:XValidation:rule="self == oldSelf",message="a Backfill's spec can't change; add another Backfill instead"
// +kubebuilder:validation:XValidation:rule="self.filingDateFrom <= self.filingDateTo",message="filingDateTo is before filingDateFrom"
type BackfillSpec struct {
	// CIK is the company's CIK, as in its submissions URL.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=9999999999
	CIK int64 `json:"cik"`

	// FilingDateFrom is the first filing date in the range, YYYY-MM-DD.
	// +kubebuilder:validation:Format=date
	FilingDateFrom string `json:"filingDateFrom"`

	// FilingDateTo is the last filing date in the range, YYYY-MM-DD.
	// +kubebuilder:validation:Format=date
	FilingDateTo string `json:"filingDateTo"`
}

// BackfillStatus is a Backfill's status, which the scheduler sets.
type BackfillStatus struct {
	// Filings is the number of filings in the range, once they're listed.
	// +optional
	// +kubebuilder:validation:Minimum=0
	Filings *int32 `json:"filings,omitempty"`

	// FilingsFetched is, of those, the filings whose fetches all succeeded,
	// or were already held.
	// +optional
	// +kubebuilder:validation:Minimum=0
	FilingsFetched *int32 `json:"filingsFetched,omitempty"`

	// FilingsFailed is, of those, the filings with a fetch that ended
	// without a version.
	// +optional
	// +kubebuilder:validation:Minimum=0
	FilingsFailed *int32 `json:"filingsFailed,omitempty"`

	// StartTime is when its first fetch was queued.
	// +optional
	StartTime Timestamp `json:"startTime,omitempty"`

	// CompletionTime is when its last filing was fetched or failed, or, in a
	// range with no filings, when they were listed.
	// +optional
	CompletionTime Timestamp `json:"completionTime,omitempty"`

	// Conditions are Complete.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// Backfill is a company's past filings, over a range of filing dates,
// fetched once.
//
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="CIK",type=integer,JSONPath=`.spec.cik`
// +kubebuilder:printcolumn:name="From",type=string,JSONPath=`.spec.filingDateFrom`
// +kubebuilder:printcolumn:name="To",type=string,JSONPath=`.spec.filingDateTo`
// +kubebuilder:printcolumn:name="Filings",type=integer,JSONPath=`.status.filings`
// +kubebuilder:printcolumn:name="Fetched",type=integer,JSONPath=`.status.filingsFetched`
// +kubebuilder:printcolumn:name="Failed",type=integer,JSONPath=`.status.filingsFailed`
// +kubebuilder:printcolumn:name="Complete",type=string,JSONPath=`.status.conditions[?(@.type=="Complete")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
type Backfill struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec BackfillSpec `json:"spec"`
	// +optional
	Status BackfillStatus `json:"status,omitempty"`
}

// BackfillList is a list of Backfills.
//
// +kubebuilder:object:root=true
type BackfillList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Backfill `json:"items"`
}
