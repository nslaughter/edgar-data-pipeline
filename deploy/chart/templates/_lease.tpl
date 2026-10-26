{{- /*
edgar.leaseTimings fails to render a Lease's timings that client-go's leader
election refuses (spec/deployment.md, LEASE-1, SCHED-3): any below 1 second,
a renewal deadline that isn't shorter than the duration, a retry period that
isn't shorter than the renewal deadline, and a renewal deadline that isn't
more than 1.2 times the retry period. It's given a dict: lease, the values
durationSeconds, renewDeadlineSeconds, and retryPeriodSeconds; name, where
the values are; and statement, the ID of the statement that holds them.
*/}}
{{- define "edgar.leaseTimings" -}}
{{- $duration := int64 .lease.durationSeconds }}
{{- $renew := int64 .lease.renewDeadlineSeconds }}
{{- $retry := int64 .lease.retryPeriodSeconds }}
{{- if or (lt $duration 1) (lt $renew 1) (lt $retry 1) }}
{{- fail (printf "%s: each timing must be at least 1 second (%s)" .name .statement) }}
{{- end }}
{{- if ge $renew $duration }}
{{- fail (printf "%s: the renewal deadline, %d seconds, must be shorter than the duration, %d (%s)" .name $renew $duration .statement) }}
{{- end }}
{{- if ge $retry $renew }}
{{- fail (printf "%s: the retry period, %d seconds, must be shorter than the renewal deadline, %d (%s)" .name $retry $renew .statement) }}
{{- end }}
{{- /* renew > 1.2 retry, in integers: 5 renew > 6 retry. */}}
{{- if le (mul 5 $renew) (mul 6 $retry) }}
{{- fail (printf "%s: the renewal deadline, %d seconds, must be more than 1.2 times the retry period, %d (%s)" .name $renew $retry .statement) }}
{{- end }}
{{- end }}
