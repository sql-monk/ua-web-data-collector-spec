# syntax=docker/dockerfile:1.7
# Quay removed the public MinIO release manifests in 2026. Build the exact, signed upstream
# release from source so a clean-host start does not depend on a disappearing vendor image.

ARG GO_IMAGE=golang:1.24.8-alpine3.22@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5
ARG RUNTIME_IMAGE=alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1

FROM ${GO_IMAGE} AS builder

ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
ARG MINIO_COMMIT=07c3a429bfed433e49018cb0f78a52145d4bedeb
ENV CGO_ENABLED=0 \
    GOBIN=/out

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go install -tags kqueue -trimpath -ldflags="-s -w \
      -X github.com/minio/minio/cmd.Version=2025-09-07T16:13:09Z \
      -X github.com/minio/minio/cmd.CopyrightYear=2025 \
      -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_VERSION} \
      -X github.com/minio/minio/cmd.CommitID=${MINIO_COMMIT} \
      -X github.com/minio/minio/cmd.ShortCommitID=07c3a429bfed" \
      "github.com/minio/minio@${MINIO_VERSION}" \
    && go version -m /out/minio | grep -F "v0.0.0-20250907161309-07c3a429bfed" \
    && module_dir="$(go env GOPATH)/pkg/mod/github.com/minio/minio@v0.0.0-20250907161309-07c3a429bfed" \
    && cp "${module_dir}/LICENSE" "${module_dir}/CREDITS" /out/

FROM ${RUNTIME_IMAGE}

ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
LABEL org.opencontainers.image.title="collector-minio" \
      org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.version="${MINIO_VERSION}"

COPY --from=builder /out/minio /usr/local/bin/minio
COPY --from=builder /out/LICENSE /out/CREDITS /licenses/
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/local/bin/minio"]
