// The transfer: the program the owner runs outside the cluster, where data/
// is, to copy the records of the git store's scope that the cluster made in
// a range of time, with their objects, into data/ (spec/snapshots.md,
// section 9, records the cluster made). It reads the cluster store of the
// context the owner names, through its API server, as a role and with a key
// of its own (spec/deployment.md, section 5, the transfer).
//
//	go run ./cmd/transfer --context CONTEXT --from TIME --until TIME [--data DIR] [--kubeconfig FILE]
//
// It exits 0 once it has copied the range, 3 when it copies nothing because
// of the store's rules, 2 when it's run wrongly, and 1 when it can't read
// the cluster store or write data/.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"time"

	"github.com/nslaughter/edgar-data-pipeline/internal/transfer"
)

// The exit statuses.
const (
	copied  = 0
	failed  = 1
	misused = 2
	refused = 3
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	status := run(ctx, os.Args[1:], os.Stdout, os.Stderr)
	stop()
	os.Exit(status)
}

func run(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("transfer", flag.ContinueOnError)
	flags.SetOutput(stderr)
	var opts transfer.Options
	var from, until string
	flags.StringVar(&opts.Context, "context", "",
		"the kube context of the cluster to read, which the transfer needs: it never reads the current context's")
	flags.StringVar(&opts.Kubeconfig, "kubeconfig", "", "the kubeconfig that holds the context, if not kubectl's")
	flags.StringVar(&opts.Data, "data", "data", "the git store's directory")
	flags.StringVar(&from, "from", "", "the start of the range, a time in RFC 3339, such as 2026-10-29T14:00:00Z")
	flags.StringVar(&until, "until", "", "the end of the range, which it doesn't include, in the same form")
	if err := flags.Parse(args); err != nil {
		return misused
	}
	if flags.NArg() > 0 {
		_, _ = fmt.Fprintf(stderr, "unexpected arguments: %v\n", flags.Args())
		return misused
	}
	if opts.Context == "" {
		_, _ = fmt.Fprintln(stderr, transfer.ErrNoContext)
		return misused
	}
	var err error
	if opts.Range.From, err = parseTime("--from", from); err != nil {
		_, _ = fmt.Fprintln(stderr, err)
		return misused
	}
	if opts.Range.Until, err = parseTime("--until", until); err != nil {
		_, _ = fmt.Fprintln(stderr, err)
		return misused
	}
	if !opts.Range.From.Before(opts.Range.Until) {
		_, _ = fmt.Fprintln(stderr, "--from must come before --until")
		return misused
	}
	err = transfer.Run(ctx, opts, stdout)
	var refusal *transfer.Refusal
	switch {
	case err == nil:
		return copied
	case errors.As(err, &refusal):
		_, _ = fmt.Fprintln(stderr, err)
		return refused
	default:
		_, _ = fmt.Fprintln(stderr, err)
		return failed
	}
}

func parseTime(name, value string) (time.Time, error) {
	if value == "" {
		return time.Time{}, fmt.Errorf("give the range with --from and --until: %s is missing", name)
	}
	t, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return time.Time{}, fmt.Errorf("%s isn't a time in RFC 3339, such as 2026-10-29T14:00:00Z: %q", name, value)
	}
	return t.UTC(), nil
}
