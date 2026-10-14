-- The notices' record: what the discoverer read from the fetcher's records,
-- the filings it found, and the views that give coverage, gaps, and lags.
-- spec/notices.md governs it (sections 4 to 10); changing it changes that spec.
-- It needs records.sql's table first. Rows are only ever added (TABLE-3), and
-- every time is kept as its source gave it: zones are read only in the views.

-- GAP-1: each poll whose event the discoverer read.
CREATE TABLE polls (
    record     bigint NOT NULL PRIMARY KEY REFERENCES records (id),
    source     text NOT NULL,
    queued_at  timestamp (3) with time zone NOT NULL,
    expires_at timestamp (3) with time zone NOT NULL
);

-- SEEN-1, FILE-3: what the discoverer read from each version of a list, a
-- header, or an index page, once for each version.
CREATE TABLE readings (
    sha256              text NOT NULL PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    kind                text NOT NULL CHECK (kind IN ('list', 'header', 'index')),
    acceptance_datetime text CHECK (acceptance_datetime ~ '^[0-9]{14}$'),
    instance            text CHECK (instance ~ '^https://www\.sec\.gov/Archives/edgar/data/'),
    error               text,
    tool                text NOT NULL CHECK (tool ~ '^discoverer@[0-9a-f]{40}$'),

    -- Only a header's reading gives an acceptance time, and gives one unless
    -- it has an error.
    CHECK (kind = 'header' OR acceptance_datetime IS NULL),
    CHECK (kind <> 'header' OR (acceptance_datetime IS NULL) = (error IS NOT NULL)),
    -- Only an index page's reading names an instance, and none with an error.
    CHECK (kind = 'index' OR instance IS NULL),
    CHECK (error IS NULL OR instance IS NULL)
);

-- SEEN-2, LIST-2: each filing a list names, with the values of its first
-- listing, as given.
CREATE TABLE filings (
    accession_number     text NOT NULL PRIMARY KEY
                         CHECK (accession_number ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
    form                 text NOT NULL,
    filing_date          text NOT NULL,
    acceptance_date_time text NOT NULL,
    listed_by            bigint NOT NULL REFERENCES records (id)
);

-- SEEN-2: for each Source and filing, the polls that bound when it first
-- showed the filing.
CREATE TABLE sightings (
    source           text NOT NULL,
    url              text NOT NULL,
    accession_number text NOT NULL REFERENCES filings,
    first_shown      bigint NOT NULL REFERENCES polls (record),
    last_not_shown   bigint REFERENCES polls (record),
    PRIMARY KEY (source, url, accession_number),
    CHECK (last_not_shown < first_shown)
);

-- LIST-1: each Backfill the scheduler ordered the discoverer to fetch.
CREATE TABLE backfills (
    uid              uuid NOT NULL PRIMARY KEY,
    name             text NOT NULL,
    cik              bigint NOT NULL CHECK (cik BETWEEN 1 AND 9999999999),
    filing_date_from date NOT NULL,
    filing_date_to   date NOT NULL,
    ordered_at       timestamp (3) with time zone NOT NULL,
    CHECK (filing_date_to >= filing_date_from)
);

-- FILE-2, FILE-3, LIST-1, LIST-2: each job the discoverer queued.
CREATE TABLE jobs (
    id               uuid NOT NULL PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('filing', 'backfill')),
    backfill         uuid REFERENCES backfills (uid),
    accession_number text REFERENCES filings,
    file             text NOT NULL
                     CHECK (file IN ('submissions', 'further', 'header', 'index', 'instance')),
    url              text NOT NULL CHECK (url ~ '^https://(www|data)\.sec\.gov/'),
    cause            bigint REFERENCES records (id),
    queued_at        timestamp (3) with time zone NOT NULL,

    -- A Backfill's job names it, and no other job does.
    CHECK ((kind = 'backfill') = (backfill IS NOT NULL)),
    -- A list's job names no filing, and only a Backfill fetches lists.
    CHECK ((file IN ('submissions', 'further')) = (accession_number IS NULL)),
    CHECK (file NOT IN ('submissions', 'further') OR kind = 'backfill'),
    -- Only the first of a Backfill's jobs has no record as its cause.
    CHECK (cause IS NOT NULL OR file = 'submissions')
);

-- LIST-2: each filing in a Backfill's range.
CREATE TABLE backfill_filings (
    backfill         uuid NOT NULL REFERENCES backfills (uid),
    accession_number text NOT NULL REFERENCES filings,
    PRIMARY KEY (backfill, accession_number)
);

-- LIST-3: each Backfill whose lists the discoverer has all read.
CREATE TABLE backfill_listings (
    backfill  uuid NOT NULL PRIMARY KEY REFERENCES backfills (uid),
    listed_at timestamp (3) with time zone NOT NULL
);

-- LIST-2, LIST-4: each of a Backfill's filings that was fetched or failed.
CREATE TABLE backfill_outcomes (
    backfill         uuid NOT NULL,
    accession_number text NOT NULL,
    outcome          text NOT NULL CHECK (outcome IN ('fetched', 'failed')),
    at               timestamp (3) with time zone NOT NULL,
    PRIMARY KEY (backfill, accession_number),
    FOREIGN KEY (backfill, accession_number) REFERENCES backfill_filings
);

-- A file's jobs and a Backfill's, a record's, and a poll's sightings.
CREATE INDEX jobs_url ON jobs (url);
CREATE INDEX jobs_accession_number ON jobs (accession_number);
CREATE INDEX jobs_backfill ON jobs (backfill);
CREATE INDEX jobs_cause ON jobs (cause);
CREATE INDEX sightings_first_shown ON sightings (first_shown);

-- FILE-6: each file of each filing that the record holds, with the first of
-- its records that names a version.
CREATE VIEW held_files (accession_number, file, url, record, sha256) AS
SELECT DISTINCT ON (j.accession_number, j.file)
       j.accession_number, j.file, j.url, r.id, r.sha256
FROM jobs j
JOIN records r ON r.url = j.url AND r.sha256 IS NOT NULL
WHERE j.accession_number IS NOT NULL
ORDER BY j.accession_number, j.file, r.id;

-- GAP-2: each gap in each Source's coverage of a URL. Its successful polls are
-- taken in the order of their records' ids. One follows the one before when
-- its job was queued less than its own window, from its queued_at to its
-- expires_at, after the earlier job expired; otherwise a gap runs from that
-- expiry to the later poll's request. After the latest, a gap is open once its
-- job has been expired for as long as its window.
CREATE VIEW gaps (source, url, after_poll, before_poll, began_at, ended_at) AS
WITH successes AS (
    SELECT p.source, r.url, p.record, r.requested_at, p.queued_at, p.expires_at,
           lag(p.record) OVER source_url AS earlier,
           lag(p.expires_at) OVER source_url AS earlier_expires_at,
           lead(p.record) OVER source_url IS NULL AS latest
    FROM polls p
    JOIN records r ON r.id = p.record
    WHERE r.sha256 IS NOT NULL
    WINDOW source_url AS (PARTITION BY p.source, r.url ORDER BY p.record)
)
SELECT source, url, earlier, record, earlier_expires_at, requested_at
FROM successes
WHERE earlier IS NOT NULL
  AND queued_at - earlier_expires_at >= expires_at - queued_at
UNION ALL
SELECT source, url, record, NULL, expires_at, NULL
FROM successes
WHERE latest
  AND now() - expires_at >= expires_at - queued_at;

-- LAG-1: a header's ACCEPTANCE-DATETIME, 14 digits with no zone (SRC-1), read
-- as US Eastern time (SRC-2, SRC-13). Null if the digits name no time.
CREATE FUNCTION header_instant(digits text) RETURNS timestamp with time zone
    LANGUAGE sql STABLE STRICT
    RETURN (
        SELECT CASE WHEN pg_input_is_valid(stamp, 'timestamp without time zone')
                    THEN stamp::timestamp without time zone AT TIME ZONE 'America/New_York'
               END
        FROM (SELECT format('%s-%s-%s %s:%s:%s', substr(digits, 1, 4), substr(digits, 5, 2),
                            substr(digits, 7, 2), substr(digits, 9, 2), substr(digits, 11, 2),
                            substr(digits, 13, 2)) AS stamp) AS formatted
    );

-- LAG-1: submissions' acceptanceDateTime, RFC 3339 (SRC-2), read as the
-- instant it states. Null if it states none.
CREATE FUNCTION listed_instant(given text) RETURNS timestamp with time zone
    LANGUAGE sql STABLE STRICT
    RETURN CASE WHEN given ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$'
                     AND pg_input_is_valid(given, 'timestamp with time zone')
                THEN given::timestamp with time zone
           END;

-- LAG-1: for each sighting, the filing's times as given, the times of the
-- polls that bound when the Source first showed it, whether a gap lies
-- between them, and the bounds of the lag from each acceptance time: more
-- than the low bound, and at most the high.
CREATE VIEW lags (
    source, url, accession_number, form, listed_acceptance, header_acceptance,
    last_not_shown, not_shown_requested_at, not_shown_received_at,
    first_shown, shown_requested_at, shown_received_at, gap,
    listed_lag_low, listed_lag_high, header_lag_low, header_lag_high
) AS
WITH headers AS (
    SELECT h.accession_number, g.acceptance_datetime
    FROM held_files h
    JOIN readings g ON g.sha256 = h.sha256
    WHERE h.file = 'header' AND g.error IS NULL
),
times AS (
    SELECT s.source, s.url, s.accession_number, f.form,
           f.acceptance_date_time AS listed_acceptance,
           hd.acceptance_datetime AS header_acceptance,
           s.last_not_shown, b.requested_at AS not_shown_requested_at,
           b.received_at AS not_shown_received_at,
           s.first_shown, a.requested_at AS shown_requested_at,
           a.received_at AS shown_received_at,
           CASE WHEN s.last_not_shown IS NOT NULL THEN EXISTS (
               SELECT FROM gaps g
               WHERE g.source = s.source AND g.url = s.url
                 AND g.after_poll >= s.last_not_shown AND g.before_poll <= s.first_shown
           ) END AS gap
    FROM sightings s
    JOIN filings f ON f.accession_number = s.accession_number
    JOIN records a ON a.id = s.first_shown
    LEFT JOIN records b ON b.id = s.last_not_shown
    LEFT JOIN headers hd ON hd.accession_number = s.accession_number
)
SELECT source, url, accession_number, form, listed_acceptance, header_acceptance,
       last_not_shown, not_shown_requested_at, not_shown_received_at,
       first_shown, shown_requested_at, shown_received_at, gap,
       not_shown_requested_at - listed_instant(listed_acceptance),
       shown_received_at - listed_instant(listed_acceptance),
       not_shown_requested_at - header_instant(header_acceptance),
       shown_received_at - header_instant(header_acceptance)
FROM times;

-- LIST-6: each Backfill's progress, as its backfill notice gives it (LIST-5).
CREATE VIEW backfill_progress (
    uid, name, started_at, listed_at, filings, fetched, failed, completed_at
) AS
SELECT b.uid, b.name, started.at, l.listed_at,
       CASE WHEN l.backfill IS NOT NULL THEN listed.filings END,
       outcomes.fetched, outcomes.failed,
       CASE WHEN l.backfill IS NOT NULL
                 AND outcomes.fetched + outcomes.failed = listed.filings
            THEN greatest(l.listed_at, outcomes.last_at)
       END
FROM backfills b
LEFT JOIN backfill_listings l ON l.backfill = b.uid
CROSS JOIN LATERAL (SELECT min(j.queued_at) AS at FROM jobs j WHERE j.backfill = b.uid) AS started
CROSS JOIN LATERAL (
    SELECT count(*) AS filings FROM backfill_filings f WHERE f.backfill = b.uid
) AS listed
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE o.outcome = 'fetched') AS fetched,
           count(*) FILTER (WHERE o.outcome = 'failed') AS failed,
           max(o.at) AS last_at
    FROM backfill_outcomes o
    WHERE o.backfill = b.uid
) AS outcomes;
