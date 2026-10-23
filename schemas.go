// Package edgar embeds the schemas in spec/schemas/, the contracts of the
// messages the pipeline's Go services exchange, so each validates them
// against the same files its specification names. It's at the module's root
// because a file can embed only files beneath its own directory.
package edgar

import "embed"

// Schemas holds spec/schemas/*.schema.json.
//
//go:embed spec/schemas/*.schema.json
var Schemas embed.FS
