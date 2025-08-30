FROM python:3.12.8-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apk add --no-cache curl bash

ARG USERNAME=runner
ARG USER_UID=1010
ARG USER_GID=$USER_UID

RUN adduser -D -u $USER_UID -h /home/$USERNAME $USERNAME && \
    chown -R $USERNAME:$USERNAME /home/$USERNAME

ENV HOME=/home/$USERNAME

USER $USERNAME

WORKDIR $HOME/app

COPY --chown=$USERNAME:$USERNAME . .

RUN uv sync --locked && \
    uv pip install -e .

EXPOSE 8003

ENTRYPOINT [ "./entrypoint.sh" ]