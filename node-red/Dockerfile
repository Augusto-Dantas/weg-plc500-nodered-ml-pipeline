FROM nodered/node-red:4.1.18

WORKDIR /data

COPY --chown=node-red:node-red package.json /data/package.json
RUN npm install --unsafe-perm --no-update-notifier --no-fund --omit=dev

COPY --chown=node-red:node-red settings.js /data/settings.js
COPY --chown=node-red:node-red flows.json /data/flows.json
COPY --chown=node-red:node-red schemas/ /data/schemas/

EXPOSE 1880
