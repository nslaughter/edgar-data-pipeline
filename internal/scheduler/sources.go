package scheduler

import (
	"context"
	"fmt"
	"math/big"
	"time"

	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// SourceReconciler keeps each Source's conditions, and gives the Poller what
// it needs to queue each Source's polls (SOURCE-1, SOURCE-3). Every Source in
// the release namespace, and the SecAccess, bear on each Source's
// conditions, so it reconciles them all at once, whichever changed.
type SourceReconciler struct {
	Client    client.Client
	Namespace string
	Poller    *Poller
}

// SetupWithManager runs the reconciler on the active replica (SCHED-4). A
// change to a Source's status alone doesn't bear on the conditions, so only
// a change to its spec, or its creation or deletion, starts it; any change
// to the SecAccess does, a stop in its status among them.
func (r *SourceReconciler) SetupWithManager(mgr ctrl.Manager) error {
	all := handler.EnqueueRequestsFromMapFunc(func(context.Context, client.Object) []reconcile.Request {
		return []reconcile.Request{{NamespacedName: types.NamespacedName{Namespace: r.Namespace, Name: "sources"}}}
	})
	return ctrl.NewControllerManagedBy(mgr).
		Named("sources").
		Watches(&v1alpha1.Source{}, all, builder.WithPredicates(predicate.GenerationChangedPredicate{})).
		Watches(&v1alpha1.SecAccess{}, all).
		Complete(r)
}

// Reconcile sets each Source's Polling and WithinBudget conditions, and
// gives the Poller each Source's Poll.
func (r *SourceReconciler) Reconcile(ctx context.Context, _ reconcile.Request) (reconcile.Result, error) {
	var sources v1alpha1.SourceList
	if err := r.Client.List(ctx, &sources, client.InNamespace(r.Namespace)); err != nil {
		return reconcile.Result{}, err
	}
	access := &v1alpha1.SecAccess{}
	if err := r.Client.Get(ctx, client.ObjectKey{Name: v1alpha1.SecAccessName}, access); apierrors.IsNotFound(err) {
		access = nil
	} else if err != nil {
		return reconcile.Result{}, err
	}

	total := Total(sources.Items)
	budget := budgetCondition(total)
	names := map[string]bool{}
	var failed error
	for i := range sources.Items {
		source := &sources.Items[i]
		names[source.Name] = true
		polling := pollingCondition(source, access)
		r.Poller.Set(source.Name, Poll{
			Polling: polling.Status == metav1.ConditionTrue,
			Feed:    source.Spec.Feed,
			URL:     source.Spec.URL,
			Cadence: time.Duration(source.Spec.CadenceSeconds) * time.Second,
		})
		if err := r.setConditions(ctx, source, polling, budget); err != nil {
			failed = err
		}
	}
	r.Poller.Retain(names)
	return reconcile.Result{}, failed
}

func (r *SourceReconciler) setConditions(ctx context.Context, source *v1alpha1.Source, conditions ...metav1.Condition) error {
	original := source.DeepCopy()
	for _, condition := range conditions {
		condition.ObservedGeneration = source.Generation
		meta.SetStatusCondition(&source.Status.Conditions, condition)
	}
	if equality.Semantic.DeepEqual(original.Status, source.Status) {
		return nil
	}
	return client.IgnoreNotFound(r.Client.Status().Patch(ctx, source, client.MergeFrom(original)))
}

// pollingCondition says whether a Source is polling: while it isn't paused
// and requests are allowed. If it isn't, the reason is the first that
// applies, in SOURCE-1's order.
func pollingCondition(source *v1alpha1.Source, access *v1alpha1.SecAccess) metav1.Condition {
	condition := metav1.Condition{Type: v1alpha1.ConditionPolling, Status: metav1.ConditionFalse}
	switch {
	case access == nil:
		condition.Reason = v1alpha1.ReasonNoSecAccess
		condition.Message = "There is no SecAccess cluster, so requests to the SEC aren't allowed."
	case access.Stopped():
		condition.Reason = v1alpha1.ReasonRequestsStopped
		condition.Message = fmt.Sprintf(
			"The SecAccess has a stop the owner hasn't acknowledged, at %s, after a 403 for %s.",
			access.Status.Stop.At, access.Status.Stop.URL)
	case access.Spec.Paused:
		condition.Reason = v1alpha1.ReasonRequestsPaused
		condition.Message = "The SecAccess is paused, so requests to the SEC aren't allowed."
	case source.Spec.Paused:
		condition.Reason = v1alpha1.ReasonPaused
		condition.Message = "The Source is paused."
	default:
		condition.Status = metav1.ConditionTrue
		condition.Reason = v1alpha1.ReasonPolling
		condition.Message = fmt.Sprintf("The scheduler queues its polls every %d seconds.", source.Spec.CadenceSeconds)
	}
	return condition
}

// budgetCondition says whether the Sources not paused poll within the
// budget, and gives their total (SOURCE-3).
func budgetCondition(total *big.Rat) metav1.Condition {
	if WithinBudget(total) {
		return metav1.Condition{
			Type:   v1alpha1.ConditionWithinBudget,
			Status: metav1.ConditionTrue,
			Reason: v1alpha1.ReasonWithinBudget,
			Message: fmt.Sprintf("The Sources not paused poll %s requests a second, within the budget of %s.",
				FormatRate(total), FormatRate(Budget)),
		}
	}
	return metav1.Condition{
		Type:   v1alpha1.ConditionWithinBudget,
		Status: metav1.ConditionFalse,
		Reason: v1alpha1.ReasonOverBudget,
		Message: fmt.Sprintf("The Sources not paused poll %s requests a second, over the budget of %s.",
			FormatRate(total), FormatRate(Budget)),
	}
}
