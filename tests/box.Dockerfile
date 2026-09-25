# Disposable image of tests/test_box.py. The test runs the box scripts as the user fedora, as on the
# rig boxes. It uses /opt/bench and the ports 8080, 8081, and 9000.
FROM fedora:44
ARG FRANKENPHP_VERSION=1.12.7
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils nginx php-fpm \
    && dnf clean all
RUN curl -fsSL -o /usr/local/bin/frankenphp \
    "https://github.com/php/frankenphp/releases/download/v${FRANKENPHP_VERSION}/frankenphp-linux-x86_64-gnu" \
    && chmod 0755 /usr/local/bin/frankenphp
RUN useradd -m fedora && install -d -o fedora -g fedora /opt/bench
USER fedora
ENV HOME=/home/fedora BOX_TEST=1
WORKDIR /repo
CMD ["python3", "-m", "unittest", "tests.test_box", "-v"]
