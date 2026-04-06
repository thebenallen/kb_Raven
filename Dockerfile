FROM kbase/sdkpython:3.8.0
MAINTAINER ac.shahnam

USER root

# Build Raven from a pinned upstream tag for reproducibility.
# Current repo and CLI verified from upstream GitHub.
ARG RAVEN_REPO=https://github.com/lbcb-sci/raven.git
ARG RAVEN_REF=1.8.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    cmake \
    ninja-build \
    build-essential \
    zlib1g-dev \
    ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --recursive ${RAVEN_REPO} raven \
 && cd /opt/raven \
 && git checkout ${RAVEN_REF} \
 && cmake -S ./ -B ./build -DRAVEN_BUILD_EXE=1 -DCMAKE_BUILD_TYPE=Release -G Ninja \
 && cmake --build ./build -j "$(nproc)" \
 && cmake --install ./build \
 && raven --version \
 && raven --help >/dev/null

# KBase module layout
COPY ./ /kb/module
RUN mkdir -p /kb/module/work
RUN chmod -R a+rw /kb/module

WORKDIR /kb/module

RUN make all

ENTRYPOINT [ "./scripts/entrypoint.sh" ]

CMD [ ]
