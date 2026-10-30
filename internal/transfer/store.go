package transfer

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/jackc/pgx/v5"
)

// ClusterStore is the cluster store, read through port-forwards that the
// transfer opens through the API server, as its role and with its key
// (TRANSFER-5, LOG-5).
type ClusterStore struct {
	database *pgx.Conn
	objects  *s3.Client
	forwards []*Forward
}

// Open opens a port-forward to the database's primary and one to Garage's
// S3 API, and connects to each with the transfer's credentials. Its caller
// closes it before the transfer exits.
func (k Kube) Open(ctx context.Context, creds Credentials) (*ClusterStore, error) {
	c := &ClusterStore{}
	database, err := k.Forward(ctx, primaryService(), DatabasePort)
	if err != nil {
		return nil, err
	}
	c.forwards = append(c.forwards, database)
	objects, err := k.Forward(ctx, S3Service, S3Port)
	if err != nil {
		c.Close()
		return nil, err
	}
	c.forwards = append(c.forwards, objects)
	config, err := databaseConfig(database.Port, creds.Password)
	if err != nil {
		c.Close()
		return nil, err
	}
	c.database, err = pgx.ConnectConfig(ctx, config)
	if err != nil {
		c.Close()
		return nil, fmt.Errorf("connecting to the database as %s: %w", Role, err)
	}
	c.objects = s3Client(fmt.Sprintf("http://127.0.0.1:%d", objects.Port), creds)
	return c, nil
}

// databaseConfig gives the connection to the database through a local port:
// to edgar, as the role transfer, with its password, over TLS, and with
// SCRAM's channel binding, as libpq's sslmode=require and
// channel_binding=require ask (TRANSFER-5). TLS doesn't check the server's
// certificate, since channel binding authenticates the server instead.
//
// Nothing of it comes from the environment or a file. pgx reads libpq's
// variables, such as PGSERVICE, which names a service file to read, and
// PGREQUIREAUTH, which no connection string can unset, so it parses the
// connection string while none of them is set. The connection string sets
// each setting that names a file, which pgx would otherwise find in the
// home directory, to the empty string, so none is read; the password is the
// one given; and it sends no parameter but its name (TRANSFER-4).
func databaseConfig(port uint16, password string) (*pgx.ConnConfig, error) {
	connString := fmt.Sprintf(
		"host=127.0.0.1 port=%d dbname=%s user=%s sslmode=require channel_binding=require "+
			"sslnegotiation=postgres passfile='' sslrootcert='' sslcert='' sslkey='' "+
			"target_session_attrs=any connect_timeout=30",
		port, Database, Role)
	var config *pgx.ConnConfig
	err := withoutLibpqEnvironment(func() (err error) {
		config, err = pgx.ParseConfig(connString)
		return err
	})
	if err != nil {
		return nil, err
	}
	config.Password = password
	config.RuntimeParams = map[string]string{"application_name": "transfer"}
	return config, nil
}

// libpqEnvironment keeps two callers from hiding libpq's variables at once.
var libpqEnvironment sync.Mutex

// withoutLibpqEnvironment runs parse with every variable whose name begins
// with PG, as each of libpq's does, taken out of the process's environment,
// and puts them back once it returns. Nothing else in the transfer reads
// them.
func withoutLibpqEnvironment(parse func() error) error {
	libpqEnvironment.Lock()
	defer libpqEnvironment.Unlock()
	hidden := map[string]string{}
	for _, variable := range os.Environ() {
		name, value, _ := strings.Cut(variable, "=")
		if strings.HasPrefix(name, "PG") {
			hidden[name] = value
		}
	}
	defer func() {
		for name, value := range hidden {
			_ = os.Setenv(name, value)
		}
	}()
	for name := range hidden {
		if err := os.Unsetenv(name); err != nil {
			return err
		}
	}
	return parse()
}

// s3Client gives a client of Garage's S3 API at the endpoint, with the
// transfer's key and in its region, and no other credentials: none from the
// environment or ~/.aws (TRANSFER-4, TRANSFER-5).
func s3Client(endpoint string, creds Credentials) *s3.Client {
	return s3.New(s3.Options{
		Region:       creds.Region,
		BaseEndpoint: aws.String(endpoint),
		UsePathStyle: true,
		Credentials:  aws.NewCredentialsCache(credentials.NewStaticCredentialsProvider(creds.KeyID, creds.KeySecret, "")),
		// As the fetcher's client: Garage's own check of an object is its
		// name, which the transfer recomputes (OBJ-1).
		RequestChecksumCalculation: aws.RequestChecksumCalculationWhenRequired,
		ResponseChecksumValidation: aws.ResponseChecksumValidationWhenRequired,
	})
}

// The columns of a record's fields, in the store's table's order.
const recordColumns = "feed, url, requested_at, received_at, attempts, status, error, final_url, date, etag, " +
	"last_modified, content_type, sha256, bytes, object, tool"

// Records gives the range's records, in the order of their ids, as one
// snapshot of the table, read in a transaction that may only read.
func (c *ClusterStore) Records(ctx context.Context, r Range) ([]Stored, error) {
	tx, err := c.database.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	// It changes nothing, so it ends with a rollback.
	defer func() { _ = tx.Rollback(ctx) }()
	rows, err := tx.Query(ctx,
		"SELECT id, "+recordColumns+" FROM records WHERE requested_at >= $1 AND requested_at < $2 ORDER BY id",
		r.From.UTC(), r.Until.UTC())
	if err != nil {
		return nil, err
	}
	var stored []Stored
	for rows.Next() {
		var s Stored
		var requested time.Time
		var received *time.Time
		rec := &s.Record
		if err := rows.Scan(&s.ID, &rec.Feed, &rec.URL, &requested, &received, &rec.Attempts, &rec.Status,
			&rec.Error, &rec.FinalURL, &rec.Date, &rec.ETag, &rec.LastModified, &rec.ContentType, &rec.SHA256,
			&rec.Bytes, &rec.Object, &rec.Tool); err != nil {
			return nil, err
		}
		rec.RequestedAt = stamp(requested)
		if received != nil {
			at := stamp(*received)
			rec.ReceivedAt = &at
		}
		stored = append(stored, s)
	}
	return stored, rows.Err()
}

// Object gives an object as the bucket holds it.
func (c *ClusterStore) Object(ctx context.Context, name string) ([]byte, error) {
	out, err := c.objects.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(Bucket), Key: aws.String(name)})
	if err != nil {
		return nil, err
	}
	defer func() { _ = out.Body.Close() }()
	return io.ReadAll(out.Body)
}

// Close closes the connection and the port-forwards.
func (c *ClusterStore) Close() {
	if c.database != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = c.database.Close(ctx)
	}
	for _, f := range c.forwards {
		_ = f.Close()
	}
}
