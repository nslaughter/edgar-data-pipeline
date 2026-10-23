package scheduler

import (
	"math/big"
	"strings"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// Budget is half the fetcher's limit of 5 requests a second, which all the
// Sources not paused share (spec/controls.md, section 2).
var Budget = big.NewRat(5, 2)

// Total is the requests a second the Sources not paused poll together: the
// sum of 1 / cadenceSeconds, exactly, as VAL-3 computes it.
func Total(sources []v1alpha1.Source) *big.Rat {
	total := new(big.Rat)
	for _, source := range sources {
		if source.Spec.Paused || source.Spec.CadenceSeconds < 1 {
			continue
		}
		total.Add(total, big.NewRat(1, int64(source.Spec.CadenceSeconds)))
	}
	return total
}

// WithinBudget says whether a total is within the budget: at most 5/2.
func WithinBudget(total *big.Rat) bool {
	return total.Cmp(Budget) <= 0
}

// FormatRate gives a rate in requests a second as a decimal, rounded to the
// fewest places, from 3 up to 20, that keep it on the same side of the
// budget, so a total over the budget never reads as the budget.
func FormatRate(rate *big.Rat) string {
	side := rate.Cmp(Budget)
	for places := 3; ; places++ {
		text := strings.TrimRight(strings.TrimRight(rate.FloatString(places), "0"), ".")
		rounded, _ := new(big.Rat).SetString(text)
		if rounded.Cmp(Budget) == side || places == 20 {
			return text
		}
	}
}
