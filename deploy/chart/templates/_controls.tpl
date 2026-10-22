{{- /*
The checks on the controls that values.schema.json can't make (spec/controls.md,
VAL-2, VAL-3). Each fails the render, so helm template, helm install, and helm
upgrade fail with it.
*/}}

{{- /*
edgar.gcd: the greatest common divisor of two integers of at least 0, by
Euclid's algorithm.
*/}}
{{- define "edgar.gcd" -}}
{{- $a := index . 0 | int64 -}}
{{- $b := index . 1 | int64 -}}
{{- if eq $b 0 -}}
{{- $a -}}
{{- else -}}
{{- include "edgar.gcd" (list $b (mod $a $b)) -}}
{{- end -}}
{{- end -}}

{{- /*
edgar.checkDay fails unless the day of a date, YYYY-MM-DD at the start of a
value the schema has checked, is in its month, as the API server's check of
the formats date and date-time requires (VAL-2). It takes the value's place in
the values, and the value.
*/}}
{{- define "edgar.checkDay" -}}
{{- $where := index . 0 -}}
{{- $value := index . 1 -}}
{{- $parts := splitList "-" (substr 0 10 $value) -}}
{{- $year := index $parts 0 | atoi -}}
{{- $month := index $parts 1 | atoi -}}
{{- $day := index $parts 2 | atoi -}}
{{- $days := index (list 31 28 31 30 31 30 31 31 30 31 30 31) (sub $month 1) -}}
{{- if and (eq $month 2) (or (and (eq (mod $year 4) 0) (ne (mod $year 100) 0)) (eq (mod $year 400) 0)) -}}
{{- $days = 29 -}}
{{- end -}}
{{- if gt $day $days -}}
{{- fail (printf "%s, %s, isn't a date: its month has %d days (VAL-2)" $where $value $days) -}}
{{- end -}}
{{- end -}}

{{- /*
edgar.checkBudget fails if the Sources not paused would together poll faster
than the budget, half the fetcher's limit: if the sum of 1 / cadenceSeconds is
more than 5/2 (VAL-3). The sum is kept as a fraction in lowest terms, so a sum
of exactly 5/2 passes. Its denominator is held below a quarter of the largest
int64, so nothing overflows; should a sum need a larger one, which takes many
cadences without a common factor, it's compared in floating point instead,
which decides any sum more than a billionth from the budget, and the chart
refuses a sum nearer than that.
*/}}
{{- define "edgar.checkBudget" -}}
{{- $limit := int64 2305843009213693951 -}}
{{- $numerator := int64 0 -}}
{{- $denominator := int64 1 -}}
{{- $exact := true -}}
{{- $approximate := 0.0 -}}
{{- range $name, $source := .sources -}}
{{- if not $source.paused -}}
{{- $cadence := int64 $source.cadenceSeconds -}}
{{- $approximate = addf $approximate (divf 1 $cadence) -}}
{{- if $exact -}}
{{- $scale := div $cadence (include "edgar.gcd" (list $denominator $cadence) | int64) -}}
{{- if gt $denominator (div $limit $scale) -}}
{{- $exact = false -}}
{{- else -}}
{{- $denominator = mul $denominator $scale -}}
{{- $numerator = add (mul $numerator $scale) (div $denominator $cadence) -}}
{{- $common := include "edgar.gcd" (list $numerator $denominator) | int64 -}}
{{- $numerator = div $numerator $common -}}
{{- $denominator = div $denominator $common -}}
{{- /* 2 n > 5 d, without overflow: n > 2 d + floor(d / 2). */ -}}
{{- if gt $numerator (add (mul $denominator 2) (div $denominator 2)) -}}
{{- fail (printf "controls.sources: the Sources not paused would together poll at least %d/%d requests a second, more than the budget of 5/2 (VAL-3)" $numerator $denominator) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not $exact -}}
{{- if gt $approximate 2.500000001 -}}
{{- fail (printf "controls.sources: the Sources not paused would together poll %v requests a second, more than the budget of 2.5 (VAL-3)" $approximate) -}}
{{- else if gt $approximate 2.499999999 -}}
{{- fail (printf "controls.sources: the Sources not paused would together poll about %v requests a second, too near the budget of 2.5 to tell whether they're within it (VAL-3)" $approximate) -}}
{{- end -}}
{{- end -}}
{{- end -}}
