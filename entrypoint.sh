#!/bin/sh
REQUIRED_VARS="GITHUB_TOKEN GITHUB_API_URL PORT"

MISSING_VARS=false

echo "Checking for required environment variables..."
for VAR_NAME in $REQUIRED_VARS; do
  # Use 'eval' to get the value of the variable whose name is stored in VAR_NAME.
  # The '-z' check will be true if the variable is unset or set to an empty string.
  VAR_VALUE=$(eval echo "\$$VAR_NAME")

  if [ -z "$VAR_VALUE" ]; then
    # If a variable is not set or is empty, print an error message.
    echo "Error: Environment variable '$VAR_NAME' is not set." >&2
    MISSING_VARS=true
  fi
done

if [ "$MISSING_VARS" = true ]; then
  echo "Please set all required environment variables before running the application." >&2
  exit 1
fi

echo "All required environment variables are set. Starting the application..."

source .venv/bin/activate

mcp-github-projects