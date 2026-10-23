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
The integers of any size that edgar.checkBudget sums with when an int64 can't
hold its sum: each is a list of its digits in base 10^9, least significant
first. Each helper takes a dict. In edgar.bigTimes, edgar.bigMod, and
edgar.bigDivide, its key n is the integer, and m an integer from 1 to
2^31 - 1, so no step passes int64's range. edgar.bigTimes, edgar.bigDivide,
and edgar.bigAdd set its key out to the integer they make.
*/}}

{{- /* edgar.bigTimes: n × m. */}}
{{- define "edgar.bigTimes" -}}
{{- $out := list -}}
{{- $carry := int64 0 -}}
{{- range .n -}}
{{- $digit := add (mul . $.m) $carry -}}
{{- $out = append $out (mod $digit 1000000000) -}}
{{- $carry = div $digit 1000000000 -}}
{{- end -}}
{{- /* The carry is less than 3 × 10^9: two digits at most. */ -}}
{{- range until 2 -}}
{{- if gt $carry 0 -}}
{{- $out = append $out (mod $carry 1000000000) -}}
{{- $carry = div $carry 1000000000 -}}
{{- end -}}
{{- end -}}
{{- $_ := set . "out" $out -}}
{{- end -}}

{{- /* edgar.bigMod: n mod m, as text. */}}
{{- define "edgar.bigMod" -}}
{{- $remainder := int64 0 -}}
{{- range reverse .n -}}
{{- $remainder = mod (add (mul $remainder 1000000000) .) $.m -}}
{{- end -}}
{{- $remainder -}}
{{- end -}}

{{- /* edgar.bigDivide: n / m, rounded down. */}}
{{- define "edgar.bigDivide" -}}
{{- $remainder := int64 0 -}}
{{- $digits := list -}}
{{- range reverse .n -}}
{{- $value := add (mul $remainder 1000000000) . -}}
{{- $digits = append $digits (div $value $.m) -}}
{{- $remainder = mod $value $.m -}}
{{- end -}}
{{- $_ := set . "out" (reverse $digits) -}}
{{- end -}}

{{- /* edgar.bigAdd: a + b. */}}
{{- define "edgar.bigAdd" -}}
{{- $out := list -}}
{{- $carry := int64 0 -}}
{{- range $i := until (max (len .a) (len .b) | int) -}}
{{- $digit := $carry -}}
{{- if lt $i (len $.a) }}{{ $digit = add $digit (index $.a $i) }}{{ end -}}
{{- if lt $i (len $.b) }}{{ $digit = add $digit (index $.b $i) }}{{ end -}}
{{- $out = append $out (mod $digit 1000000000) -}}
{{- $carry = div $digit 1000000000 -}}
{{- end -}}
{{- if gt $carry 0 }}{{ $out = append $out $carry }}{{ end -}}
{{- $_ := set . "out" $out -}}
{{- end -}}

{{- /* edgar.bigMore: whether a is more than b, as text. */}}
{{- define "edgar.bigMore" -}}
{{- $length := max (len .a) (len .b) | int -}}
{{- $order := 0 -}}
{{- range $i := until $length -}}
{{- $at := sub (sub $length 1) $i | int -}}
{{- $x := 0 }}{{ if lt $at (len $.a) }}{{ $x = index $.a $at }}{{ end -}}
{{- $y := 0 }}{{ if lt $at (len $.b) }}{{ $y = index $.b $at }}{{ end -}}
{{- if eq $order 0 -}}
{{- if gt $x $y }}{{ $order = 1 }}{{ else if lt $x $y }}{{ $order = -1 }}{{ end -}}
{{- end -}}
{{- end -}}
{{- gt $order 0 -}}
{{- end -}}

{{- /* edgar.bigText: n in decimal. */}}
{{- define "edgar.bigText" -}}
{{- $text := "" -}}
{{- range reverse .n -}}
{{- if ne $text "" }}{{ $text = printf "%s%09d" $text . }}{{ else if gt . 0 }}{{ $text = printf "%d" . }}{{ end -}}
{{- end -}}
{{- default "0" $text -}}
{{- end -}}

{{- /*
edgar.checkBudget fails if the Sources not paused would together poll faster
than the budget, half the fetcher's limit: if the sum of 1 / cadenceSeconds is
more than 5/2 (VAL-3). The sum is kept as a fraction in lowest terms, so a sum
of exactly 5/2 passes. Its denominator is held below a quarter of the largest
int64, so nothing overflows; should a sum need a larger one, which takes many
cadences without a common factor, it's summed again with integers of any size,
as n/d, where d is the least common multiple of the cadences, and n the sum of
d / cadenceSeconds, so it's exact still.
*/}}
{{- define "edgar.checkBudget" -}}
{{- $limit := int64 2305843009213693951 -}}
{{- $numerator := int64 0 -}}
{{- $denominator := int64 1 -}}
{{- $exact := true -}}
{{- range $name, $source := .sources -}}
{{- if not $source.paused -}}
{{- $cadence := int64 $source.cadenceSeconds -}}
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
{{- $multiple := list (int64 1) -}}
{{- range $name, $source := .sources -}}
{{- if not $source.paused -}}
{{- $cadence := int64 $source.cadenceSeconds -}}
{{- $remainder := include "edgar.bigMod" (dict "n" $multiple "m" $cadence) | int64 -}}
{{- $product := dict "n" $multiple "m" (div $cadence (include "edgar.gcd" (list $remainder $cadence) | int64)) -}}
{{- $_ := include "edgar.bigTimes" $product -}}
{{- $multiple = $product.out -}}
{{- end -}}
{{- end -}}
{{- $sum := list (int64 0) -}}
{{- range $name, $source := .sources -}}
{{- if not $source.paused -}}
{{- $quotient := dict "n" $multiple "m" (int64 $source.cadenceSeconds) -}}
{{- $_ := include "edgar.bigDivide" $quotient -}}
{{- $total := dict "a" $sum "b" $quotient.out -}}
{{- $_ = include "edgar.bigAdd" $total -}}
{{- $sum = $total.out -}}
{{- end -}}
{{- end -}}
{{- /* 2 n > 5 d. */ -}}
{{- $twice := dict "n" $sum "m" 2 -}}
{{- $_ := include "edgar.bigTimes" $twice -}}
{{- $budget := dict "n" $multiple "m" 5 -}}
{{- $_ = include "edgar.bigTimes" $budget -}}
{{- if eq (include "edgar.bigMore" (dict "a" $twice.out "b" $budget.out)) "true" -}}
{{- fail (printf "controls.sources: the Sources not paused would together poll %s/%s requests a second, more than the budget of 5/2 (VAL-3)" (include "edgar.bigText" (dict "n" $sum)) (include "edgar.bigText" (dict "n" $multiple))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
