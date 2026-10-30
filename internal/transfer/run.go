package transfer

import (
	"context"
	"errors"
	"fmt"
	"io"
	"slices"
)

// Options are what the owner gives the transfer when they run it.
type Options struct {
	// Context is the kube context of the cluster to read (TRANSFER-3), and
	// Kubeconfig the file that holds it, or "" for those kubectl reads.
	Context, Kubeconfig string
	// Data is the git store's directory, data/.
	Data string
	// Range is the range of time whose records it copies (XFER-1).
	Range Range
}

// Run copies a range from the cluster store of the context named into data/,
// and reports what it read and what it copied to out. It reads the range in
// a cluster that holds the fake EDGAR too, but copies nothing from it, and
// says so whatever else it ends with (TRANSFER-6). It returns a *Refusal
// when it copies nothing because of the store's rules.
func Run(ctx context.Context, opts Options, out io.Writer) error {
	config, err := RESTConfig(opts.Kubeconfig, opts.Context)
	if err != nil {
		return err
	}
	kube, err := NewKube(config)
	if err != nil {
		return err
	}
	fake, err := kube.FakeEDGAR(ctx)
	if err != nil {
		return err
	}
	creds, err := kube.Credentials(ctx)
	if err != nil {
		return withFakeEDGAR(fake, err)
	}
	store, err := kube.Open(ctx, creds)
	if err != nil {
		return withFakeEDGAR(fake, err)
	}
	defer store.Close()
	return copyRange(ctx, store, GitStore{Dir: opts.Data}, opts.Range, fake, out)
}

// copyRange plans the copy of a range and makes it, unless the cluster
// holds the fake EDGAR.
func copyRange(ctx context.Context, source Source, git GitStore, r Range, fake bool, out io.Writer) error {
	c, err := Plan(ctx, source, git, r)
	if err != nil {
		return withFakeEDGAR(fake, err)
	}
	_, _ = fmt.Fprintf(out, "Read %d records %s: %d in the scope, of which data/ holds %d already.\n",
		c.Read, r, c.InScope, c.Held)
	_, _ = fmt.Fprintf(out, "To copy: %d records, with %d objects that data/ doesn't hold.\n",
		c.Records(), c.Objects())
	if fake {
		return withFakeEDGAR(fake, nil)
	}
	if err := c.Write(); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(out, "Copied %d records, with %d objects, into %s.\n", c.Records(), c.Objects(), git.Dir)
	return nil
}

// withFakeEDGAR gives what the transfer ends with: the error given, and, in
// a cluster that holds the fake EDGAR, that it does, which alone is reason
// to copy nothing (TRANSFER-6). With the fake EDGAR, a refusal gives it as
// one more reason, and nothing else to end with is a refusal of its own.
func withFakeEDGAR(fake bool, err error) error {
	if !fake {
		return err
	}
	reason := fmt.Sprintf("the fake EDGAR is installed in the namespace %s of the context's cluster, so its "+
		"records are of fetches the fake EDGAR answered, not the SEC (TRANSFER-6)", Namespace)
	var refusal *Refusal
	switch {
	case err == nil:
		return &Refusal{Reasons: []string{reason}}
	case errors.As(err, &refusal):
		return &Refusal{Reasons: append(slices.Clone(refusal.Reasons), reason)}
	default:
		return fmt.Errorf("%w; and %s", err, reason)
	}
}
