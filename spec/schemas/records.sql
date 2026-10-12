-- The cluster store's records: one row per fetch the fetcher makes.
-- spec/snapshots.md governs it (sections 4 and 6); changing it changes that spec.
-- Each column but id and job is a field of a record, with the same name.

CREATE TABLE records (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job           uuid NOT NULL UNIQUE,
    feed          text NOT NULL CHECK (feed ~ '^[a-z0-9][a-z0-9-]*$'),
    url           text NOT NULL,
    requested_at  timestamp (3) with time zone NOT NULL,
    received_at   timestamp (3) with time zone,
    attempts      integer NOT NULL CHECK (attempts >= 1),
    status        integer,
    error         text,
    final_url     text,
    date          text,
    etag          text,
    last_modified text,
    content_type  text,
    sha256        text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    bytes         bigint CHECK (bytes >= 0),
    object        text,
    tool          text NOT NULL CHECK (tool ~ '^fetcher@[0-9a-f]{40}$'),

    -- REC-3: sha256, bytes, and object are all set or all null, and agree.
    CHECK ((sha256 IS NULL) = (bytes IS NULL) AND (sha256 IS NULL) = (object IS NULL)),
    CHECK (object = 'objects/' || substr(sha256, 1, 2) || '/' || sha256 || '.gz'),
    -- REC-4: a 200 with no error names a body.
    CHECK (status IS DISTINCT FROM 200 OR error IS NOT NULL OR sha256 IS NOT NULL),
    -- REC-5: a 304 that names no version has an error.
    CHECK (status IS DISTINCT FROM 304 OR sha256 IS NOT NULL OR error IS NOT NULL),
    -- REC-6: only a 200 with no error, or a 304, names a body. The comparisons
    -- are null-safe, so a row with no status can't name one.
    CHECK (sha256 IS NULL OR (status IS NOT DISTINCT FROM 200 AND error IS NULL)
           OR status IS NOT DISTINCT FROM 304),
    -- REC-7: no response means an error is recorded.
    CHECK (status IS NOT NULL OR error IS NOT NULL),
    -- REC-11: received_at is set exactly when status is, and date only then.
    CHECK ((received_at IS NULL) = (status IS NULL)),
    CHECK (status IS NOT NULL OR date IS NULL)
);

-- FETCH-4 and REC-5: each URL's latest earlier record with a version.
CREATE INDEX records_url_id ON records (url, id);
