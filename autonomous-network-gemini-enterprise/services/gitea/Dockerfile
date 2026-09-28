FROM gitea/gitea:latest
USER root
COPY init.sh /init.sh
RUN chmod +x /init.sh
ENTRYPOINT ["/init.sh"]

