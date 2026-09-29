# Quality-gate tools, pinned and checksum-verified, for every DevCrew sandbox image:
#   gitleaks (secret scanning) and osv-scanner (dependency vulnerabilities).
# Built first by scripts/build_sandbox_images.sh as devcrew-sandbox-gate-tools; the sandbox
# images copy /gate-tools from it. To upgrade, change the URLs and their sha256 checksums.
ARG TARGETARCH=amd64

FROM scratch AS amd64
ADD --checksum=sha256:fa0500f6b7e41d28791ebc680f5dd9899cd42b58629218a5f041efa899151a8e \
    https://github.com/gitleaks/gitleaks/releases/download/v8.24.2/gitleaks_8.24.2_linux_x64.tar.gz \
    /gate-tools/gitleaks.tar.gz
ADD --checksum=sha256:3abcfd7126c453a00421487e721b296e0cb68085bd431d6cef60872774170fc8 \
    https://github.com/google/osv-scanner/releases/download/v2.0.2/osv-scanner_linux_amd64 \
    /gate-tools/osv-scanner

FROM scratch AS arm64
ADD --checksum=sha256:574a6d52573c61173add7ddb5e3cc68c0e82cb0735818a1eeb9a0a2de1643fbc \
    https://github.com/gitleaks/gitleaks/releases/download/v8.24.2/gitleaks_8.24.2_linux_arm64.tar.gz \
    /gate-tools/gitleaks.tar.gz
ADD --checksum=sha256:5da413cb77ddb99bd115961e25ddf02490f6e1415abea1e15c9557057a457c08 \
    https://github.com/google/osv-scanner/releases/download/v2.0.2/osv-scanner_linux_arm64 \
    /gate-tools/osv-scanner

FROM ${TARGETARCH}
