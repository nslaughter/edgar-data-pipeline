-- The views Grafana reads: each Source's coverage, its gaps, and its lags,
-- made for the dashboards from the notices' record. spec/telemetry.md governs
-- it (section 8); changing it changes that spec. It needs records.sql's and
-- notices.sql's tables and views first. Grafana's role may read these views
-- and nothing else in the database (VIEW-3), so each reads the record as the
-- views' owner does, and none sets security_invoker.

CREATE SCHEMA dashboards;

-- VIEW-4: each stretch of a Source's coverage of a URL between its gaps, as
-- notices.md's section 5 defines coverage. A stretch begins at the request of
-- the Source's first successful poll of the URL, or of a poll that ended a
-- gap, and ends where the next gap began, or, with none after it, when the job
-- of its latest successful poll expired. So the stretches and the gaps follow
-- one definition, GAP-2's, in public.gaps.
CREATE VIEW dashboards.coverage (source, url, began_at, ended_at) AS
WITH successes AS (
    SELECT p.source, r.url, p.record, p.expires_at
    FROM public.polls p
    JOIN public.records r ON r.id = p.record
    WHERE r.sha256 IS NOT NULL
),
starts AS (
    SELECT source, url, min(record) AS record
    FROM successes
    GROUP BY source, url
    UNION ALL
    SELECT source, url, before_poll
    FROM public.gaps
    WHERE before_poll IS NOT NULL
)
SELECT s.source, s.url, r.requested_at,
       coalesce(
           (SELECT g.began_at
            FROM public.gaps g
            WHERE g.source = s.source AND g.url = s.url AND g.after_poll >= s.record
            ORDER BY g.after_poll
            LIMIT 1),
           (SELECT x.expires_at
            FROM successes x
            WHERE x.source = s.source AND x.url = s.url
            ORDER BY x.record DESC
            LIMIT 1))
FROM starts s
JOIN public.records r ON r.id = s.record;

-- VIEW-5: each gap in a Source's coverage of a URL, ended or open, with its
-- length in seconds: to its end, or, while it's open, to when the view is read.
CREATE VIEW dashboards.gaps (source, url, began_at, ended_at, seconds) AS
SELECT source, url, began_at, ended_at,
       extract(epoch FROM coalesce(ended_at, now()) - began_at)
FROM public.gaps;

-- VIEW-6: each sighting that has a last_not_shown, so whose lag the record
-- bounds, with the bounds of the lag from each acceptance time in seconds, as
-- public.lags gives them (LAG-1).
CREATE VIEW dashboards.lags (
    source, url, accession_number, form, shown_received_at, gap,
    listed_lag_low_seconds, listed_lag_high_seconds,
    header_lag_low_seconds, header_lag_high_seconds
) AS
SELECT source, url, accession_number, form, shown_received_at, gap,
       extract(epoch FROM listed_lag_low), extract(epoch FROM listed_lag_high),
       extract(epoch FROM header_lag_low), extract(epoch FROM header_lag_high)
FROM public.lags
WHERE last_not_shown IS NOT NULL;
