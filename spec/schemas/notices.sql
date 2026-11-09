-- The notices' record: what the discoverer read from the fetcher's records,
-- the filings it found, and the views that give coverage, gaps, lags, and each
-- day's check against its master index. spec/notices.md governs it (sections
-- 4 to 10); changing it changes that spec. It needs records.sql's table first.
-- Rows are only ever added (TABLE-3), and every time is kept as its source
-- gave it: zones are read only in the views and the functions they call.

-- GAP-1: each poll whose event the discoverer read.
CREATE TABLE polls (
    record     bigint NOT NULL PRIMARY KEY REFERENCES records (id),
    source     text NOT NULL,
    queued_at  timestamp (3) with time zone NOT NULL,
    expires_at timestamp (3) with time zone NOT NULL
);

-- SEEN-1, FEED-1, DAY-1, DAY-3, FILE-3: what the discoverer read from each
-- version of a list, the feed, a listing, a master index, a header, or an
-- index page, once for each version.
CREATE TABLE readings (
    sha256              text NOT NULL PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    kind                text NOT NULL
                        CHECK (kind IN ('list', 'feed', 'listing', 'master', 'header', 'index')),
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

-- GAP-4: each successful poll of the feed, with whether it reaches back to
-- the successful poll before it: whether both versions are read as the feed
-- and name a filing in common.
CREATE TABLE feed_polls (
    record       bigint NOT NULL PRIMARY KEY REFERENCES polls (record),
    previous     bigint REFERENCES polls (record),
    reaches_back boolean NOT NULL,

    CHECK (previous < record),
    -- A poll reaches back only to one before it.
    CHECK (previous IS NOT NULL OR NOT reaches_back)
);

-- SEEN-2, LIST-2, DAY-3: each filing a list, the feed, or a master index
-- names, with the values of the version it was first added from, as given. A
-- later list can give it another acceptanceDateTime (SRC-24), which this table
-- doesn't keep, and a master index gives it no time.
CREATE TABLE filings (
    accession_number     text NOT NULL PRIMARY KEY
                         CHECK (accession_number ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
    form                 text NOT NULL,
    filing_date          text NOT NULL,
    acceptance_date_time text,
    listed_by            bigint NOT NULL REFERENCES records (id)
);

-- FEED-2: each entry of each filing, a party of it, as the first version of
-- the feed that the discoverer read naming the filing gives it (SRC-27,
-- SRC-33).
CREATE TABLE entries (
    accession_number text NOT NULL REFERENCES filings,
    position         integer NOT NULL CHECK (position >= 1),
    listed_by        bigint NOT NULL REFERENCES polls (record),
    form             text NOT NULL,
    party            text NOT NULL,
    cik              text NOT NULL CHECK (cik ~ '^[0-9]{10}$'),
    role             text NOT NULL,
    link             text NOT NULL
                     CHECK (link ~ '^https://www\.sec\.gov/Archives/edgar/data/[1-9][0-9]*/[0-9]{18}/[0-9]{10}-[0-9]{2}-[0-9]{6}-index\.htm$'),
    filed            text NOT NULL CHECK (filed ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    updated          text NOT NULL,
    PRIMARY KEY (accession_number, position)
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

-- DAY-1: each day a listing shows: each master index it names (SRC-34).
CREATE TABLE listing_days (
    sha256 text NOT NULL REFERENCES readings,
    day    date NOT NULL,
    PRIMARY KEY (sha256, day)
);

-- DAY-3: each filing a master index lists, with the values of the first line
-- that names it, as given (SRC-35).
CREATE TABLE index_filings (
    sha256           text NOT NULL REFERENCES readings,
    accession_number text NOT NULL REFERENCES filings,
    cik              text NOT NULL CHECK (cik ~ '^[1-9][0-9]*$'),
    form             text NOT NULL,
    date_filed       text NOT NULL CHECK (date_filed ~ '^[0-9]{8}$'),
    PRIMARY KEY (sha256, accession_number)
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

-- FILE-2, FILE-3, LIST-1, LIST-2, DAY-2, DAY-4: each job the discoverer queued.
CREATE TABLE jobs (
    id               uuid NOT NULL PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('filing', 'backfill')),
    backfill         uuid REFERENCES backfills (uid),
    accession_number text REFERENCES filings,
    file             text NOT NULL
                     CHECK (file IN ('submissions', 'further', 'master', 'header', 'index',
                                     'instance')),
    url              text NOT NULL CHECK (url ~ '^https://(www|data)\.sec\.gov/'),
    cause            bigint REFERENCES records (id),
    queued_at        timestamp (3) with time zone NOT NULL,

    -- A Backfill's job names it, and no other job does.
    CHECK ((kind = 'backfill') = (backfill IS NOT NULL)),
    -- A list's job and a master index's name no filing. Only a Backfill
    -- fetches lists, and only the daily check, in jobs of kind filing, fetches
    -- master indexes.
    CHECK ((file IN ('submissions', 'further', 'master')) = (accession_number IS NULL)),
    CHECK (file NOT IN ('submissions', 'further') OR kind = 'backfill'),
    CHECK (file <> 'master' OR kind = 'filing'),
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

-- A file's jobs and a Backfill's, a record's, and a poll's sightings; the
-- feed's filings by their filing date, and a filing's master indexes.
CREATE INDEX jobs_url ON jobs (url);
CREATE INDEX jobs_accession_number ON jobs (accession_number);
CREATE INDEX jobs_backfill ON jobs (backfill);
CREATE INDEX jobs_cause ON jobs (cause);
CREATE INDEX sightings_first_shown ON sightings (first_shown);
CREATE INDEX entries_filed ON entries (filed);
CREATE INDEX index_filings_accession_number ON index_filings (accession_number);

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
-- expiry to the later poll's request. A poll of the feed that doesn't reach
-- back to the one before (GAP-4) leaves a gap from the earlier poll's request
-- to its own instead, whether or not it follows. After the latest, a gap is
-- open once its job has been expired for as long as its window.
CREATE VIEW gaps (source, url, after_poll, before_poll, began_at, ended_at) AS
WITH successes AS (
    SELECT p.source, r.url, p.record, r.requested_at, p.queued_at, p.expires_at,
           lag(p.record) OVER source_url AS earlier,
           lag(p.expires_at) OVER source_url AS earlier_expires_at,
           lag(r.requested_at) OVER source_url AS earlier_requested_at,
           lead(p.record) OVER source_url IS NULL AS latest,
           f.record IS NOT NULL
               AND NOT (f.reaches_back AND f.previous = lag(p.record) OVER source_url)
               AS falls_short
    FROM polls p
    JOIN records r ON r.id = p.record
    LEFT JOIN feed_polls f ON f.record = p.record
    WHERE r.sha256 IS NOT NULL
    WINDOW source_url AS (PARTITION BY p.source, r.url ORDER BY p.record)
)
SELECT source, url, earlier, record,
       CASE WHEN falls_short THEN earlier_requested_at ELSE earlier_expires_at END,
       requested_at
FROM successes
WHERE earlier IS NOT NULL
  AND (falls_short OR queued_at - earlier_expires_at >= expires_at - queued_at)
UNION ALL
SELECT source, url, record, NULL, expires_at, NULL
FROM successes
WHERE latest
  AND now() - expires_at >= expires_at - queued_at;

-- LAG-1: a header's ACCEPTANCE-DATETIME, 14 digits with no zone (SRC-1), read
-- as US Eastern time (SRC-25). Null if the digits name no time.
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

-- LAG-1: submissions' acceptanceDateTime, RFC 3339 (SRC-25), read as the
-- instant it states, which is later than the header's by the Eastern offset
-- on its date (SRC-25); or the feed's updated, with its offset (SRC-27), read
-- so too. Null if it states none.
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

-- DAY-2: the first day checked: the day after the date, in US Eastern time
-- (SRC-34), of the request of the feed's first poll, by record id, whose
-- version is read as the feed without an error. Null until there's one.
CREATE FUNCTION first_day_checked() RETURNS date
    LANGUAGE sql STABLE
    RETURN (
        SELECT (r.requested_at AT TIME ZONE 'America/New_York')::date + 1
        FROM feed_polls f
        JOIN records r ON r.id = f.record
        JOIN readings g ON g.sha256 = r.sha256
        WHERE g.kind = 'feed' AND g.error IS NULL
        ORDER BY f.record
        LIMIT 1
    );

-- DAY-5: each day checked, by the first record of its master index's URL whose
-- version is read as a master index without an error, with the number of
-- filings that version lists, of those that no version of the feed named, and
-- of the filings the feed named with the day as their filing date that no
-- master index of a day checked lists.
CREATE VIEW checks (day, url, record, filings, index_only, feed_only) AS
WITH masters AS (
    SELECT DISTINCT ON (j.url)
           to_date(substring(j.url FROM 'master\.([0-9]{8})\.idx$'), 'YYYYMMDD') AS day,
           j.url, r.id AS record, r.sha256
    FROM jobs j
    JOIN records r ON r.url = j.url
    JOIN readings g ON g.sha256 = r.sha256
    WHERE j.file = 'master' AND g.kind = 'master' AND g.error IS NULL
    ORDER BY j.url, r.id
)
SELECT m.day, m.url, m.record,
       (SELECT count(*) FROM index_filings i WHERE i.sha256 = m.sha256),
       (SELECT count(*)
        FROM index_filings i
        WHERE i.sha256 = m.sha256
          AND NOT EXISTS (SELECT FROM entries e WHERE e.accession_number = i.accession_number)),
       (SELECT count(DISTINCT e.accession_number)
        FROM entries e
        WHERE e.filed = to_char(m.day, 'YYYY-MM-DD')
          AND NOT EXISTS (
              SELECT FROM masters x
              JOIN index_filings i ON i.sha256 = x.sha256
              WHERE i.accession_number = e.accession_number))
FROM masters m;

-- DAY-6: each quarter's listing that a Source's polls fetched, with the last
-- day of its quarter, and when the record first held a successful poll of it,
-- requested after the next quarter's first day ended in US Eastern time, whose
-- version is read as a listing, with each day it shows from the first day
-- checked a day checked: the latest of that poll's received_at and those days'
-- records', for the earliest such poll.
CREATE VIEW listings (url, quarter_ends, read_through_at) AS
WITH quarters AS (
    SELECT DISTINCT r.url,
           (make_date(substring(r.url FROM '/daily-index/([0-9]{4})/')::integer,
                      substring(r.url FROM '/QTR([1-4])/')::integer * 3, 1)
            + interval '1 month - 1 day')::date AS quarter_ends
    FROM polls p
    JOIN records r ON r.id = p.record
    WHERE r.url ~ '^https://www\.sec\.gov/Archives/edgar/daily-index/[0-9]{4}/QTR[1-4]/index\.json$'
),
through AS (
    SELECT q.url,
           greatest(r.received_at,
                    (SELECT max(k.received_at)
                     FROM listing_days d
                     JOIN checks c ON c.day = d.day
                     JOIN records k ON k.id = c.record
                     WHERE d.sha256 = r.sha256 AND d.day >= first_day_checked())) AS at
    FROM quarters q
    JOIN records r ON r.url = q.url
    JOIN polls p ON p.record = r.id
    JOIN readings g ON g.sha256 = r.sha256
    WHERE g.kind = 'listing' AND g.error IS NULL
      AND r.requested_at >= (q.quarter_ends + 2)::timestamp AT TIME ZONE 'America/New_York'
      AND NOT EXISTS (
          SELECT FROM listing_days d
          WHERE d.sha256 = r.sha256 AND d.day >= first_day_checked()
            AND NOT EXISTS (SELECT FROM checks c WHERE c.day = d.day))
)
SELECT q.url, q.quarter_ends, (SELECT min(x.at) FROM through x WHERE x.url = q.url)
FROM quarters q;
