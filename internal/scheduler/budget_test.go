package scheduler

import (
	"math/big"
	"testing"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

func sources(cadences ...int32) []v1alpha1.Source {
	var all []v1alpha1.Source
	for _, cadence := range cadences {
		all = append(all, v1alpha1.Source{Spec: v1alpha1.SourceSpec{CadenceSeconds: cadence}})
	}
	return all
}

func repeat(n int, cadence int32) []int32 {
	cadences := make([]int32, n)
	for i := range cadences {
		cadences[i] = cadence
	}
	return cadences
}

// spec: SOURCE-3
func TestTheSourcesAreWithinTheBudgetUpToFiveHalvesARequestASecondExactly(t *testing.T) {
	for _, c := range []struct {
		name     string
		cadences []int32
		within   bool
	}{
		{"25 Sources at 10 seconds", repeat(25, 10), true},
		{"26 Sources at 10 seconds", repeat(26, 10), false},
		{"two at 1 second and one at 2", []int32{1, 1, 2}, true},
		{"three at 1 second", []int32{1, 1, 1}, false},
		{"seven at 3 seconds and one at 6", append(repeat(7, 3), 6), true},
		{"seven at 3 seconds and one at 5", append(repeat(7, 3), 5), false},
		{"none", nil, true},
	} {
		if got := WithinBudget(Total(sources(c.cadences...))); got != c.within {
			t.Errorf("%s: within the budget %v, want %v", c.name, got, c.within)
		}
	}
}

// spec: SOURCE-3
func TestThePausedSourcesDontCountTowardTheBudget(t *testing.T) {
	all := sources(1, 1, 1)
	all[2].Spec.Paused = true
	if total := Total(all); total.Cmp(big.NewRat(2, 1)) != 0 {
		t.Fatalf("total %s, want 2", total)
	}
}

// spec: SOURCE-3
func TestATotalOverTheBudgetNeverReadsAsTheBudget(t *testing.T) {
	for _, c := range []struct {
		total *big.Rat
		want  string
	}{
		{big.NewRat(3, 10), "0.3"},
		{big.NewRat(5, 2), "2.5"},
		{big.NewRat(1, 3), "0.333"},
		{big.NewRat(2501, 1000), "2.501"},
		{new(big.Rat).Add(big.NewRat(5, 2), big.NewRat(1, 2147483647)), "2.5000000005"},
		{new(big.Rat).Sub(big.NewRat(5, 2), big.NewRat(1, 2147483647)), "2.4999999995"},
	} {
		if got := FormatRate(c.total); got != c.want {
			t.Errorf("%s reads %q, want %q", c.total, got, c.want)
		}
	}
}
