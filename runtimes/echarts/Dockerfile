FROM mcr.microsoft.com/playwright:v1.48.1-noble
ENV TZ=UTC NODE_ENV=production
RUN groupadd -g 65532 nonroot && useradd -u 65532 -g nonroot -M nonroot
COPY package.json /runtime/package.json
RUN cd /runtime && npm install --omit=dev --ignore-scripts
COPY entrypoint.mjs /runtime/entrypoint
RUN chmod 0555 /runtime/entrypoint && mkdir -p /output && chown 65532:65532 /output
USER 65532:65532
ENTRYPOINT ["node", "/runtime/entrypoint"]
