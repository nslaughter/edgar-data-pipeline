// Package v1alpha1 holds the Go types of the pipeline's controls: the custom
// resources Source, Backfill, and SecAccess, at version v1alpha1 of the API
// group edgar.nathanslaughter.com (spec/controls.md, section 4). controller-gen
// generates their definitions from these types, which are the definitions in
// spec/schemas/ apart from their descriptions and annotations (API-2).
//
// +kubebuilder:object:generate=true
// +groupName=edgar.nathanslaughter.com
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

var (
	// GroupVersion is the resources' API group and version.
	GroupVersion = schema.GroupVersion{Group: "edgar.nathanslaughter.com", Version: "v1alpha1"}

	// SchemeBuilder adds the resources' types to a scheme.
	SchemeBuilder = runtime.NewSchemeBuilder(addKnownTypes)

	// AddToScheme adds the resources' types to a scheme.
	AddToScheme = SchemeBuilder.AddToScheme
)

func addKnownTypes(scheme *runtime.Scheme) error {
	scheme.AddKnownTypes(GroupVersion,
		&Source{}, &SourceList{}, &Backfill{}, &BackfillList{}, &SecAccess{}, &SecAccessList{})
	metav1.AddToGroupVersion(scheme, GroupVersion)
	return nil
}

// Timestamp is a time in UTC, RFC 3339 with milliseconds and Z, such as
// 2026-10-06T18:04:12.345Z (spec/controls.md, section 2).
//
// +kubebuilder:validation:Format=date-time
// +kubebuilder:validation:Pattern=`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$`
type Timestamp string

// TimestampLayout formats a time.Time as a Timestamp, once it's in UTC.
const TimestampLayout = "2006-01-02T15:04:05.000Z"
