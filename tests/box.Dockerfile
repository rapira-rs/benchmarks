# Disposable image of tests/test_box.py. The test runs the box scripts as the user fedora, as on the
# rig boxes. It uses /opt/bench and the port 8080.
FROM fedora:44
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils \
    && dnf clean all
RUN useradd -m fedora && install -d -o fedora -g fedora /opt/bench
USER fedora
ENV HOME=/home/fedora BOX_TEST=1
WORKDIR /repo
CMD ["python3", "-m", "unittest", "tests.test_box", "-v"]
