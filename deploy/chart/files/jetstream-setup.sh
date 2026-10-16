#!/bin/sh
# Makes each stream the umbrella chart's values list, and its durable
# consumers, or updates them to the values, through JetStream's API
# (spec/deployment.md, NATS-3, NATS-5). The chart runs it as a hook Job after
# each install and upgrade, with what it reads in $CONFIG:
#
#   streams                  the streams' names, in the order they're made
#   stream-NAME.json         a stream's configuration
#   consumers-NAME           the names of a stream's consumers
#   consumer-NAME-DURABLE.json   a consumer's configuration
#
# A stream kept by interest keeps a message only for the consumers it has
# when the message is published. So such a stream is made with only its
# name as its subject, JetStream's default, and takes its subjects only once
# its consumers exist: until then, a message published to them is refused.
set -eu

CONFIG=${CONFIG:-/config}
# JetStream's error code for a stream that doesn't exist.
STREAM_NOT_FOUND=10059

# Sends one request to JetStream's API, and prints the response. Fails when
# the request or the reading of its response fails, and on an error in the
# response, unless its code is one of those given after the body. Each
# failure returns explicitly, since set -e doesn't apply where the function is
# a condition, as in the wait below.
request() {
	subject=$1
	body=$2
	shift 2
	response=$(nats --server "$NATS_URL" --timeout 10s request --raw "\$JS.API.$subject" "$body") || return 1
	code=$(printf '%s' "$response" | jq -r '.error.err_code // empty') || return 1
	if [ -n "$code" ]; then
		for allowed in "$@"; do
			if [ "$code" = "$allowed" ]; then
				printf '%s' "$response"
				return 0
			fi
		done
		echo "$subject: $response" >&2
		return 1
	fi
	printf '%s' "$response"
}

# Waits until JetStream accepts requests: until its meta group has a leader.
attempt=0
until request INFO '' >/dev/null 2>&1; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 60 ]; then
		echo "JetStream accepted no request" >&2
		request INFO ''
	fi
	sleep 5
done

while read -r stream; do
	config=$(cat "$CONFIG/stream-$stream.json")
	retention=$(printf '%s' "$config" | jq -r .retention)
	info=$(request "STREAM.INFO.$stream" '' "$STREAM_NOT_FOUND")
	exists=$(printf '%s' "$info" | jq -r 'has("config")')

	if [ "$exists" = false ]; then
		if [ "$retention" = interest ]; then
			request "STREAM.CREATE.$stream" "$(printf '%s' "$config" | jq -c 'del(.subjects)')" >/dev/null
		else
			request "STREAM.CREATE.$stream" "$config" >/dev/null
		fi
		echo "Created stream $stream"
	elif [ "$retention" != interest ]; then
		request "STREAM.UPDATE.$stream" "$config" >/dev/null
		echo "Updated stream $stream"
	fi

	while read -r consumer; do
		request "CONSUMER.CREATE.$stream.$consumer" "$(cat "$CONFIG/consumer-$stream-$consumer.json")" >/dev/null
		echo "Made consumer $consumer of $stream"
	done <"$CONFIG/consumers-$stream"

	if [ "$retention" = interest ]; then
		request "STREAM.UPDATE.$stream" "$config" >/dev/null
		echo "Updated stream $stream, with its subjects"
	fi
done <"$CONFIG/streams"
