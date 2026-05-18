FROM apify/actor-node-playwright-chrome:22-1.60.0

USER root
WORKDIR /home/myuser/src

RUN mkdir -p /home/myuser/src \
    && chown -R myuser:myuser /home/myuser

USER myuser

COPY --chown=myuser:myuser package*.json ./
RUN npm ci --omit=dev

COPY --chown=myuser:myuser . ./

CMD ["npm", "start"]
