package fetcher

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Records is the cluster store's table of records, to which the fetcher adds
// one record for each fetch (STORE-1, FETCH-14).
type Records interface {
	// ByJob gives the record of a job's fetch, or nil if the table holds
	// none (JOB-6).
	ByJob(ctx context.Context, job string) (*Stored, error)
	// LatestVersion gives the latest record of a URL that names a version,
	// or nil if it has none (FETCH-4).
	LatestVersion(ctx context.Context, url string) (*Version, error)
	// Add adds the record of a job's fetch, and gives its id. It returns
	// ErrRecorded if the table already holds the job's record, since no two
	// records share a job (STORE-1).
	Add(ctx context.Context, job string, record Record) (int64, error)
}

// ErrRecorded says that the table already holds the record of a job.
var ErrRecorded = errors.New("the table already holds the job's record")

// Objects is the cluster store's bucket, where the fetcher keeps each body
// as an object (OBJ-4).
type Objects interface {
	// Put stores an object under its name, unless an object has that name,
	// since the same name means the same body (OBJ-4).
	Put(ctx context.Context, name string, data []byte) error
}

// PostgresRecords is the table records in the pipeline's database, as the
// fetcher's role reaches it, which may insert and select records and
// nothing more (LOG-3, PG-8).
type PostgresRecords struct {
	Pool *pgxpool.Pool
}

// ConnectPostgres gives a pool of connections to the database, made as
// they're needed, so the fetcher starts before the database does. Each
// verifies the server's certificate and name against the certificate
// authority in the file ca (FETCHER-7).
func ConnectPostgres(ctx context.Context, host string, port int, database, user, password, ca string) (*pgxpool.Pool,
	error) {
	config, err := pgxpool.ParseConfig(fmt.Sprintf("host=%s port=%d dbname=%s user=%s sslmode=verify-full sslrootcert=%s",
		host, port, database, user, ca))
	if err != nil {
		return nil, err
	}
	config.ConnConfig.Password = password
	return pgxpool.NewWithConfig(ctx, config)
}

// The columns of a record's fields, in the store's table's order.
const recordColumns = "feed, url, requested_at, received_at, attempts, status, error, final_url, date, etag, " +
	"last_modified, content_type, sha256, bytes, object, tool"

// ByJob gives the record of a job's fetch.
func (p PostgresRecords) ByJob(ctx context.Context, job string) (*Stored, error) {
	var stored Stored
	var requested time.Time
	var received *time.Time
	r := &stored.Record
	err := p.Pool.QueryRow(ctx, "SELECT id, "+recordColumns+" FROM records WHERE job = $1", job).Scan(
		&stored.ID, &r.Feed, &r.URL, &requested, &received, &r.Attempts, &r.Status, &r.Error, &r.FinalURL, &r.Date,
		&r.ETag, &r.LastModified, &r.ContentType, &r.SHA256, &r.Bytes, &r.Object, &r.Tool)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	r.RequestedAt = timestamp(requested)
	if received != nil {
		r.ReceivedAt = ptr(timestamp(*received))
	}
	return &stored, nil
}

// LatestVersion gives the URL's latest record that names a version, by the
// order the records were added in, which their ids give.
func (p PostgresRecords) LatestVersion(ctx context.Context, url string) (*Version, error) {
	var v Version
	err := p.Pool.QueryRow(ctx, "SELECT etag, last_modified, sha256, bytes, object FROM records"+
		" WHERE url = $1 AND sha256 IS NOT NULL ORDER BY id DESC LIMIT 1", url).Scan(
		&v.ETag, &v.LastModified, &v.SHA256, &v.Bytes, &v.Object)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &v, nil
}

// Add inserts the record of a job's fetch.
func (p PostgresRecords) Add(ctx context.Context, job string, r Record) (int64, error) {
	requested, err := parseTimestamp(r.RequestedAt)
	if err != nil {
		return 0, err
	}
	var received *time.Time
	if r.ReceivedAt != nil {
		t, err := parseTimestamp(*r.ReceivedAt)
		if err != nil {
			return 0, err
		}
		received = &t
	}
	var id int64
	err = p.Pool.QueryRow(ctx, "INSERT INTO records (job, "+recordColumns+")"+
		" VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17) RETURNING id",
		job, r.Feed, r.URL, requested, received, r.Attempts, r.Status, r.Error, r.FinalURL, r.Date, r.ETag,
		r.LastModified, r.ContentType, r.SHA256, r.Bytes, r.Object, r.Tool).Scan(&id)
	var refused *pgconn.PgError
	if errors.As(err, &refused) && refused.Code == "23505" && refused.ConstraintName == "records_job_key" {
		return 0, ErrRecorded
	}
	return id, err
}

// S3Objects is the store's bucket in Garage, reached through its S3 API with
// the fetcher's key, which may read and write it and no other bucket
// (GARAGE-4, LOG-4).
type S3Objects struct {
	Client *s3.Client
	Bucket string
}

// NewS3Objects gives the bucket at Garage's S3 API, at endpoint, in its
// region, through the key whose ID and secret are given (FETCHER-6).
func NewS3Objects(endpoint, region, bucket, keyID, secret string) S3Objects {
	client := s3.New(s3.Options{
		Region:       region,
		BaseEndpoint: aws.String(endpoint),
		UsePathStyle: true,
		Credentials:  aws.NewCredentialsCache(credentials.NewStaticCredentialsProvider(keyID, secret, "")),
		// The checksums the SDK adds by default aren't part of the S3 API
		// every server keeps to; Garage's own check is the object's name,
		// which a check of the store recomputes (STORE-3).
		RequestChecksumCalculation: aws.RequestChecksumCalculationWhenRequired,
		ResponseChecksumValidation: aws.ResponseChecksumValidationWhenRequired,
	})
	return S3Objects{Client: client, Bucket: bucket}
}

// Put stores the object, after looking that no object has its name. Only the
// active fetcher writes to the bucket, so none is stored between the look and
// the write but one of the same body, by the replica before it.
func (o S3Objects) Put(ctx context.Context, name string, data []byte) error {
	_, err := o.Client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(o.Bucket), Key: aws.String(name)})
	if err == nil {
		return nil
	}
	if !notFound(err) {
		return err
	}
	_, err = o.Client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:        aws.String(o.Bucket),
		Key:           aws.String(name),
		Body:          bytes.NewReader(data),
		ContentLength: aws.Int64(int64(len(data))),
		ContentType:   aws.String("application/gzip"),
	})
	return err
}

func notFound(err error) bool {
	var missing *types.NotFound
	var response *smithyhttp.ResponseError
	return errors.As(err, &missing) || errors.As(err, &response) && response.HTTPStatusCode() == http.StatusNotFound
}
