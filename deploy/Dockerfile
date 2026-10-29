FROM debian:bookworm-slim AS build
ARG BUILD_JOBS=16
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential clang-19 llvm-19 lld-19 pkg-config libssl-dev libarchive-dev libyaml-dev zlib1g-dev bzip2 \
    ca-certificates python3 curl && rm -rf /var/lib/apt/lists/*
# Keep the inspector's regexp engine aligned with the current SDK.
RUN curl --fail --location --retry 3 \
    https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.bz2 \
    --output /tmp/pcre2-10.47.tar.bz2
RUN echo '47fe8c99461250d42f89e6e8fdaeba9da057855d06eb7fc08d9ca03fd08d7bc7  /tmp/pcre2-10.47.tar.bz2' | sha256sum -c - \
    && cd /tmp && tar -xjf pcre2-10.47.tar.bz2 && cd pcre2-10.47 \
    && ./configure --prefix=/opt/pcre2 --enable-unicode --enable-jit --disable-pcre2grep --disable-pcre2test \
    && make -j${BUILD_JOBS} && make install
ENV PKG_CONFIG_PATH=/opt/pcre2/lib/pkgconfig LD_LIBRARY_PATH=/opt/pcre2/lib
WORKDIR /src
ENV CXX=clang++-19 SPUTNIK_NATIVE_CXX=clang++-19 SPUTNIK_NATIVE_JOBS=8
COPY sputnik-lang/ sputnik-lang/
RUN make -C sputnik-lang -j${BUILD_JOBS} build/sputnik
COPY orbit/ orbit/
COPY sqlite3/ sqlite3/
COPY sputnik-registry/src/ sputnik-registry/src/
COPY sputnik-registry/tools/build sputnik-registry/tools/build.s sputnik-registry/tools/
COPY sputnik-registry/migrations/ sputnik-registry/migrations/
ENV SPUTNIK_NATIVE_RT_JOBS=8
RUN --mount=type=cache,target=/src/sputnik-registry/.build/native-runtime \
    cd sputnik-registry && (./tools/build || \
    { cat .build/compiler.stderr.log .build/build-result.json 2>/dev/null; exit 1; })

FROM build AS test
COPY sputnik-registry/test/ /src/sputnik-registry/test/
ENV MALLOC_ARENA_MAX=2
RUN --mount=type=cache,target=/src/sputnik-registry/.build/native-runtime \
    cd /src/sputnik-lang && SPUTNIK_NATIVE_RT_CACHE=/src/sputnik-registry/.build/native-runtime \
    python3 tests/native_parallel_test.py build/sputnik build/native-parallel-test
RUN cd /src/sputnik-lang && python3 tests/package_registry_cli_test.py build/sputnik
RUN cd /src/sputnik-lang && make -j8 build/package_selection_tests build/package_store_tests && build/package_selection_tests \
    && python3 tests/package_native_registry_cli_test.py build/sputnik build/package_store_tests
RUN --mount=type=cache,target=/src/sputnik-registry/.build/native-runtime \
    ln -s /usr/bin/clang-19 /usr/local/bin/clang \
    && ln -s /usr/bin/clang++-19 /usr/local/bin/clang++ \
    && cd /src/sputnik-registry && python3 test/integration.py \
    && python3 test/storage.py && python3 test/storage.py --native
COPY SOURCE-MANIFEST.json /src/SOURCE-MANIFEST.json

FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 libarchive13 libyaml-0-2 zlib1g libstdc++6 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10097 registry \
    && useradd --uid 10097 --gid 10097 --no-create-home --shell /usr/sbin/nologin registry
COPY --from=build /opt/pcre2/lib/libpcre2-8.so* /usr/local/lib/
RUN ldconfig
WORKDIR /opt/registry
COPY --from=test /src/sputnik-registry/.build/native/registry /usr/local/bin/registry
COPY --from=test /src/sputnik-registry/migrations/ migrations/
COPY --from=test /src/sputnik-registry/.build/inputs.json /src/sputnik-registry/.build/build-result.json \
    /src/sputnik-registry/.build/integration-native.json \
    /src/sputnik-registry/.build/native/registry.native.cpp.compile.json /src/SOURCE-MANIFEST.json evidence/
USER 10097:10097
ENV SPUTNIK_REGISTRY_CONFIG=/etc/sputnik-registry/config.json MALLOC_ARENA_MAX=2
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8080/health/ready || exit 1
ENTRYPOINT ["/usr/local/bin/registry"]
CMD ["serve"]
