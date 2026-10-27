# The fetcher's image (spec/deployment.md, FETCHER-1, IMG-5).
# scripts/build_images.py builds it from the files of the commit it tags. Its
# base images are named by their tags and digests (PIN-4), go.sum pins each
# module it's built from, and it's built with the Go its base image holds, the
# toolchain go.mod names, so the same commit builds the same software.
FROM docker.io/library/golang:1.26.5-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS build

ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY schemas.go ./
COPY spec/schemas/ spec/schemas/
COPY api/ api/
COPY internal/ internal/
COPY cmd/ cmd/
RUN go build -trimpath -ldflags=-s -o /fetcher ./cmd/fetcher

# Only the binary, which embeds the schemas in spec/schemas/ it validates
# jobs against, on an image without a shell. The image holds the system's
# certificate roots, against which the fetcher checks the SEC's hosts'
# certificates unless it's given another to check them against (FETCHER-13).
FROM gcr.io/distroless/static-debian13:nonroot@sha256:d62067879de890a1def9da0e7766b7d20810a5e5555cef8506b752437d7a3e88

COPY --from=build /fetcher /fetcher
USER 65532:65532
EXPOSE 8080 8081
ENTRYPOINT ["/fetcher"]
