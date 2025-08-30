"""
GitHub GraphQL API client for the GitHub Projects V2 MCP Server.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class GitHubClientError(Exception):
    """Custom exception for GitHubClient errors."""

    pass


class GitHubClient:
    """Client for interacting with the GitHub GraphQL API."""

    def __init__(self, token: Optional[str] = None):
        """Initialize the GitHub client.

        Args:
            token: GitHub personal access token. If None, it will use the GITHUB_TOKEN env var.
        """
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GitHub token is required")

        self.api_url = os.environ.get("GITHUB_API_URL") or "https://api.github.com/graphql"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v4+json",
        }

    def _find_case_insensitive_key(
        self, dictionary: Dict[str, Any], key: str
    ) -> Optional[str]:
        """Find a key in a dictionary case-insensitively.

        Args:
            dictionary: Dictionary to search in
            key: Key to find (case-insensitive)

        Returns:
            The actual key if found, None otherwise
        """
        if not key:
            return None

        for dict_key in dictionary:
            if dict_key and key and dict_key.lower() == key.lower():
                return dict_key
        return None

    async def execute_query(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a GraphQL query against the GitHub API.

        Args:
            query: The GraphQL query string
            variables: Variables for the GraphQL query

        Returns:
            The parsed JSON response data

        Raises:
            GitHubClientError: If the query fails or returns errors.
        """
        query_variables = variables or {}

        payload = {"query": query, "variables": query_variables}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url, headers=self.headers, json=payload, timeout=30.0
                )
                response.raise_for_status()  # Raise HTTP errors
                result = response.json()

                # Check for errors AND the presence of data
                if "errors" in result:
                    data = result.get("data")
                    if data is None:
                        # No data returned, errors are fatal
                        error_message = f"GraphQL query failed with errors and returned no data: {result['errors']}"
                        logger.error(error_message)
                        raise GitHubClientError(error_message)
                    else:
                        # Data IS present, log errors as warnings but proceed
                        logger.warning(
                            f"GraphQL query returned errors but also data: {result['errors']}"
                        )

                # If we reach here, either there were no errors, or there were errors but also data.
                data = result.get("data")
                if data is None:
                    # This case should now only happen if there were no errors but still no data.
                    raise GitHubClientError(
                        "GraphQL query returned no data and no errors."
                    )

                return data  # Return data
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP error executing GraphQL query: {e.response.status_code} - {e.response.text}"
            logger.error(error_message)
            raise GitHubClientError(error_message) from e
        except Exception as e:
            error_message = f"Unexpected error executing GraphQL query: {str(e)}"
            logger.error(error_message)
            raise GitHubClientError(error_message) from e

    async def get_projects(self, owner: str) -> List[Dict[str, Any]]:
        """Get Projects V2 for an organization or user.

        Args:
            owner: The GitHub organization or user name

        Returns:
            List of projects

        Raises:
            GitHubClientError: If the owner is not found or projects cannot be retrieved.
        """
        # First determine if this is a user or organization
        query = """
        query GetOwnerType($login: String!) {
          organization(login: $login) {
            id
            login
            __typename
          }
          user(login: $login) {
            id
            login
            __typename
          }
        }
        """

        variables = {"login": owner}

        try:
            result = await self.execute_query(query, variables)
        except GitHubClientError as e:
            logger.error(f"Failed to determine owner type for {owner}: {e}")
            raise  # Re-raise the error

        # Determine if the owner is a user or organization
        owner_type = None
        owner_id = None

        if result.get("organization"):
            owner_type = "organization"
            owner_id = result["organization"]["id"]
        elif result.get("user"):
            owner_type = "user"
            owner_id = result["user"]["id"]
        else:
            error_message = f"Owner {owner} not found or type could not be determined."
            logger.error(error_message)
            raise GitHubClientError(error_message)

        # Now get the projects based on owner type
        if owner_type == "organization":
            query = """
            query GetOrgProjects($login: String!, $first: Int!) {
              organization(login: $login) {
                projectsV2(first: $first) {
                  nodes {
                    id
                    number
                    title
                    shortDescription
                    url
                    closed
                    public
                  }
                }
              }
            }
            """

            variables = {"login": owner, "first": 50}

            try:
                result = await self.execute_query(query, variables)
                if not result.get("organization") or not result["organization"].get(
                    "projectsV2"
                ):
                    raise GitHubClientError(
                        f"Could not retrieve projects for organization {owner}"
                    )
                return result["organization"]["projectsV2"]["nodes"]
            except GitHubClientError as e:
                logger.error(f"Failed to get projects for organization {owner}: {e}")
                raise

        elif owner_type == "user":
            query = """
            query GetUserProjects($login: String!, $first: Int!) {
              user(login: $login) {
                projectsV2(first: $first) {
                  nodes {
                    id
                    number
                    title
                    shortDescription
                    url
                    closed
                    public
                  }
                }
              }
            }
            """

            variables = {"login": owner, "first": 50}

            try:
                result = await self.execute_query(query, variables)
                if not result.get("user") or not result["user"].get("projectsV2"):
                    raise GitHubClientError(
                        f"Could not retrieve projects for user {owner}"
                    )
                return result["user"]["projectsV2"]["nodes"]
            except GitHubClientError as e:
                logger.error(f"Failed to get projects for user {owner}: {e}")
                raise

        # This part should be unreachable if owner_type is determined correctly
        raise GitHubClientError(f"Unexpected error retrieving projects for {owner}")

    async def get_project_node_id(self, owner: str, project_number: int) -> str:
        """Get the node ID of a project.

        Args:
            owner: The GitHub organization or user name
            project_number: The project number

        Returns:
            The project node ID

        Raises:
            GitHubClientError: If the project is not found.
        """
        # First determine if this is a user or organization
        query = """
        query GetProjectId($login: String!, $number: Int!) {
          organization(login: $login) {
            projectV2(number: $number) {
              id
            }
          }
          user(login: $login) {
            projectV2(number: $number) {
              id
            }
          }
        }
        """

        variables = {"login": owner, "number": project_number}

        try:
            result = await self.execute_query(query, variables)
        except GitHubClientError as e:
            logger.error(
                f"Failed to query project ID for {owner}/{project_number}: {e}"
            )
            raise

        if result.get("organization") and result["organization"].get("projectV2"):
            return result["organization"]["projectV2"]["id"]
        elif result.get("user") and result["user"].get("projectV2"):
            return result["user"]["projectV2"]["id"]
        else:
            error_message = f"Project {project_number} not found for owner {owner}."
            logger.error(error_message)
            raise GitHubClientError(error_message)

    async def get_project_fields_details(
        self, owner: str, project_number: int
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get fields for a GitHub Project V2, returning a structured dictionary.
        Args:
            owner: The GitHub organization or user name
            project_number: The project number
        Returns:
            Dictionary mapping field name to its details (id, type, options).
        Raises:
            GitHubClientError: If project or fields cannot be retrieved.
        """
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot get fields details: {e}")
            raise

        query = """
        query GetProjectFields($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              fields(first: 50) {
                nodes {
                  ... on ProjectV2Field { id name __typename }
                  ... on ProjectV2IterationField {
                     id name __typename
                     configuration { iterations { id title startDate duration } }
                  }
                  ... on ProjectV2SingleSelectField {
                     id name __typename
                     options { id name color description }
                  }
                  # Add other field types if needed
                }
              }
            }
          }
        }
        """
        variables = {"projectId": project_id}

        try:
            result = await self.execute_query(query, variables)
            if not result.get("node") or not result["node"].get("fields"):
                raise GitHubClientError(
                    f"Could not retrieve fields for project {owner}/{project_number}"
                )

            fields_nodes = result["node"]["fields"]["nodes"]
            field_details_map: Dict[str, Dict[str, Any]] = {}
            for field in fields_nodes:
                field_name = field.get("name")
                if field_name:
                    options_map = {}
                    if field.get("options"):
                        options_map = {
                            opt["name"]: opt["id"] for opt in field["options"]
                        }
                    iterations_map = {}
                    if field.get(
                        "__typename"
                    ) == "ProjectV2IterationField" and field.get(
                        "configuration", {}
                    ).get(
                        "iterations"
                    ):
                        iterations = field.get("configuration", {}).get(
                            "iterations", []
                        )
                        iterations_map = {
                            iter["title"]: iter["id"] for iter in iterations
                        }

                    field_details_map[field_name] = {
                        "id": field.get("id"),
                        "type": field.get("__typename"),
                        "options": options_map,  # Map Name -> ID
                        "iterations": iterations_map,
                    }
            return field_details_map
        except GitHubClientError as e:
            logger.error(
                f"Failed to get fields details for project {owner}/{project_number}: {e}"
            )
            raise
        except Exception as e:  # Catch potential errors during processing
            logger.error(
                f"Unexpected error processing fields for project {owner}/{project_number}: {e}"
            )
            raise GitHubClientError(
                f"Could not process fields for project {owner}/{project_number}"
            )

    async def get_project_items(
        self,
        owner: str,
        project_number: int,
        limit: int = 10,
        state: Optional[str] = None,
        filter_field_name: Optional[str] = None,
        filter_field_value: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get items in a GitHub Project V2, optionally filtering by state or a custom field value.
        Args:
            owner: The GitHub organization or user name
            project_number: The project number
            limit: Maximum number of items to return per page (default: 10)
            state: Optional state to filter items by (e.g., "OPEN", "CLOSED").
            filter_field_name: Optional name of a custom field to filter by (e.g., "Status").
            filter_field_value: Optional value of the custom field to filter by (e.g., "Backlog").
            cursor: Optional cursor for pagination (default: None for first page)
        Returns:
            Dictionary containing:
                - items: List of project items
                - pageInfo: Information about pagination (hasNextPage, endCursor)
        Raises:
            GitHubClientError: If project or items cannot be retrieved, or filter is invalid.
            ValueError: If filter parameters are invalid.
        """
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot get items: {e}")
            raise

        # When filtering, we need to fetch more items since many will be excluded
        # Increase the fetch size to be more efficient
        fetch_limit = limit
        if filter_field_name and filter_field_value:
            # Be more aggressive but respect GitHub's 100 record limit
            fetch_limit = min(max(limit * 5, 50), 100)
            logger.debug(
                f"Filtering enabled: increasing fetch limit from {limit} to {fetch_limit}"
            )

        # Prepare variables dict before use
        variables: Dict[str, Any] = {"projectId": project_id, "first": fetch_limit}

        # Add cursor if provided (for pagination)
        after_clause = ""
        if cursor:
            variables["cursor"] = cursor
            after_clause = ", after: $cursor"

        # Base query definition including fragments
        field_values_fragment = """
        fragment FieldValuesFragment on ProjectV2ItemFieldValueConnection {
            nodes {
               ... on ProjectV2ItemFieldTextValue { __typename text field { ... on ProjectV2FieldCommon { name } } }
               ... on ProjectV2ItemFieldDateValue { __typename date field { ... on ProjectV2FieldCommon { name } } }
               ... on ProjectV2ItemFieldSingleSelectValue { __typename name field { ... on ProjectV2FieldCommon { name } } }
               ... on ProjectV2ItemFieldNumberValue { __typename number field { ... on ProjectV2FieldCommon { name } } }
               ... on ProjectV2ItemFieldIterationValue { __typename title startDate duration field { ... on ProjectV2FieldCommon { name } } }
            }
        }
        """
        content_fragment = """
        fragment ContentFragment on ProjectV2ItemContent {
           ... on Issue { __typename id number title state url repository { name owner { login } } }
           ... on PullRequest { __typename id number title state url repository { name owner { login } } }
           ... on DraftIssue { __typename id title body }
        }
        """

        # Build filter parameters if needed
        filter_conditions = []

        if state:
            if state.upper() not in ["OPEN", "CLOSED"]:
                raise ValueError("Invalid state filter. Must be 'OPEN' or 'CLOSED'.")
            # For state filtering, let's collect all items and filter afterwards
            # as the API has changed how filtering works

        # Variables for field-based filtering
        field_id_var = None
        option_id_var = None

        if filter_field_name and filter_field_value:
            try:
                all_fields = await self.get_project_fields_details(
                    owner, project_number
                )
                logger.debug(f"All fields available: {list(all_fields.keys())}")

                # First try exact match
                field_info = all_fields.get(filter_field_name)
                # If not found, try case-insensitive match
                if not field_info:
                    actual_field_name = self._find_case_insensitive_key(
                        all_fields, filter_field_name
                    )
                    if actual_field_name:
                        field_info = all_fields.get(actual_field_name)
                        logger.info(
                            f"Found field '{actual_field_name}' using case-insensitive match for '{filter_field_name}'"
                        )

                if not field_info:
                    raise ValueError(f"Field '{filter_field_name}' not found.")
                field_id = field_info["id"]
                field_type = field_info["type"]

                logger.debug(
                    f"Found field '{filter_field_name}' with ID {field_id} and type {field_type}"
                )

                if field_type == "ProjectV2SingleSelectField":
                    available_options = list(field_info.get("options", {}).keys())
                    logger.debug(
                        f"Available options for '{filter_field_name}': {available_options}"
                    )

                    # First try exact match
                    option_id = field_info.get("options", {}).get(filter_field_value)
                    # If not found, try case-insensitive match
                    if not option_id:
                        options = field_info.get("options", {})
                        actual_option_name = self._find_case_insensitive_key(
                            options, filter_field_value
                        )
                        if actual_option_name:
                            option_id = options.get(actual_option_name)
                            logger.info(
                                f"Found option '{actual_option_name}' using case-insensitive match for '{filter_field_value}'"
                            )

                    if not option_id:
                        raise ValueError(
                            f"Option '{filter_field_value}' not found for field '{filter_field_name}'. Available: {available_options}"
                        )
                    field_id_var = field_id
                    option_id_var = option_id
                    logger.debug(
                        f"Using field ID {field_id_var} with option ID {option_id_var} for filtering"
                    )
                elif field_type == "ProjectV2IterationField":
                    available_iterations = list(field_info.get("iterations", {}).keys())
                    logger.debug(
                        f"Available iterations for '{filter_field_name}': {available_iterations}"
                    )

                    # First try exact match
                    iteration_id = field_info.get("iterations", {}).get(
                        filter_field_value
                    )
                    # If not found, try case-insensitive match
                    if not iteration_id:
                        iterations = field_info.get("iterations", {})
                        actual_iteration_name = self._find_case_insensitive_key(
                            iterations, filter_field_value
                        )
                        if actual_iteration_name:
                            iteration_id = iterations.get(actual_iteration_name)
                            logger.info(
                                f"Found iteration '{actual_iteration_name}' using case-insensitive match for '{filter_field_value}'"
                            )

                    if not iteration_id:
                        raise ValueError(
                            f"Iteration '{filter_field_value}' not found for field '{filter_field_name}'. Available: {available_iterations}"
                        )
                    field_id_var = field_id
                    option_id_var = iteration_id
                    logger.debug(
                        f"Using field ID {field_id_var} with iteration ID {option_id_var} for filtering"
                    )
                else:
                    logger.warning(
                        f"Filtering by field type '{field_type}' is not yet implemented."
                    )
            except GitHubClientError as e:
                logger.error(f"Error during field lookup for filtering: {e}")
                raise
            except ValueError as e:
                logger.error(f"Invalid filter input: {e}")
                raise

        # Use single curly braces for GraphQL, not double curly braces for f-string
        query = f"""
        {field_values_fragment}
        {content_fragment}
        query GetProjectItems($projectId: ID!, $first: Int!{', $cursor: String' if cursor else ''}) {{
          node(id: $projectId) {{
            ... on ProjectV2 {{
              items(first: $first{after_clause}) {{
                pageInfo {{
                  hasNextPage
                  endCursor
                }}
                nodes {{
                  id
                  type
                  fieldValues(first: 20) {{ ...FieldValuesFragment }}
                  content {{ ...ContentFragment }}
                }}
              }}
            }}
          }}
        }}
        """

        logger.debug(f"Executing items query: {query} with vars: {variables}")

        try:
            result = await self.execute_query(query, variables)
            if result is None:
                logger.warning(
                    f"Query returned None result for project {owner}/{project_number}"
                )
                return {
                    "items": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }

            items_data = result.get("node", {}).get("items")
            if items_data is None:  # Check if items key exists, even if null
                if result.get("node") is None:
                    raise GitHubClientError(
                        f"Project node not found for {owner}/{project_number}"
                    )
                else:
                    logger.info(
                        f"No items found matching filter criteria for project {owner}/{project_number}"
                    )
                    return {
                        "items": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }

            # Get pagination info
            page_info = items_data.get("pageInfo", {})
            items = items_data.get("nodes", [])

            logger.debug(
                f"Retrieved {len(items)} items from project {owner}/{project_number}"
            )

            # Process field values
            filtered_items = []
            for item in items:
                if item.get("fieldValues") and item["fieldValues"].get("nodes"):
                    field_values = item["fieldValues"]["nodes"]

                    processed_values = {}
                    matches_field_filter = (
                        False if (field_id_var and option_id_var) else True
                    )

                    for fv in field_values:
                        raw_field_name = fv.get("field", {}).get("name")
                        # Sanitize the field name
                        if raw_field_name:
                            # Remove chars other than alphanumeric, space, underscore, hyphen
                            sanitized_field_name = re.sub(
                                r"[^\w\s-]", "", raw_field_name
                            ).strip()
                        else:
                            sanitized_field_name = "UnknownField"

                        field_name = (
                            sanitized_field_name or "UnnamedField"
                        )  # Ensure not empty

                        value = "N/A"
                        fv_type = fv.get("__typename")
                        if fv_type == "ProjectV2ItemFieldTextValue":
                            value = fv.get("text", "N/A")
                        elif fv_type == "ProjectV2ItemFieldDateValue":
                            value = fv.get("date", "N/A")
                        elif fv_type == "ProjectV2ItemFieldSingleSelectValue":
                            value = fv.get("name", "N/A")
                            # Check if this is the field we're filtering on
                            if (
                                field_id_var
                                and option_id_var
                                and field_name
                                and filter_field_name
                                and field_name.lower() == filter_field_name.lower()
                                and value
                                and filter_field_value
                                and value.lower() == filter_field_value.lower()
                            ):
                                matches_field_filter = True
                                logger.debug(
                                    f"Found matching item with field '{field_name}' = '{value}'"
                                )
                            elif (
                                field_id_var
                                and option_id_var
                                and field_name
                                and filter_field_name
                                and field_name.lower() == filter_field_name.lower()
                            ):
                                logger.debug(
                                    f"Field name matched but value did not: '{value}' != '{filter_field_value}'"
                                )
                        elif fv_type == "ProjectV2ItemFieldNumberValue":
                            value = fv.get("number", "N/A")
                        elif fv_type == "ProjectV2ItemFieldIterationValue":
                            title = fv.get("title", "N/A")
                            value = f"{title} (Start: {fv.get('startDate', 'N/A')})"
                            # Check if this is the field we're filtering on
                            if (
                                field_id_var
                                and option_id_var
                                and field_name
                                and filter_field_name
                                and field_name.lower() == filter_field_name.lower()
                                and title
                                and filter_field_value
                                and title.lower() == filter_field_value.lower()
                            ):
                                matches_field_filter = True
                                logger.debug(
                                    f"Found matching item with iteration field '{field_name}' = '{title}'"
                                )
                        processed_values[field_name] = value

                    item["fieldValues"] = processed_values

                    # Apply state filter if needed
                    matches_state_filter = True
                    if state and item.get("content"):
                        content_state = item["content"].get("state")
                        if content_state and content_state != state.upper():
                            matches_state_filter = False

                    if matches_field_filter and matches_state_filter:
                        filtered_items.append(item)
                else:
                    # Items without field values are included only if we're not doing field filtering
                    if not (field_id_var and option_id_var):
                        filtered_items.append(item)

            # When filtering, we may have fetched more than requested, so trim to the requested limit
            if filter_field_name and filter_field_value and len(filtered_items) > limit:
                filtered_items = filtered_items[:limit]
                # Update pagination info to indicate there may be more filtered results
                page_info = {
                    "hasNextPage": True,
                    "endCursor": page_info.get("endCursor"),
                }

            # Emergency check: if we're filtering and got very few results, warn that there might be more
            if (
                filter_field_name
                and filter_field_value
                and len(filtered_items) == 0
                and len(items) >= fetch_limit
                and fetch_limit < 100
            ):
                logger.warning(
                    f"Found 0 filtered items but fetched the maximum ({fetch_limit}). There might be more items beyond this limit. Consider increasing the search scope."
                )

            logger.debug(
                f"Filtered down to {len(filtered_items)} items for project {owner}/{project_number} using criteria field_name={filter_field_name}, field_value={filter_field_value}"
            )
            return {"items": filtered_items, "pageInfo": page_info}
        except GitHubClientError as e:
            logger.error(
                f"Failed to get items for project {owner}/{project_number}: {e}"
            )
            raise

    async def create_issue(
        self, owner: str, repo: str, title: str, body: str = ""
    ) -> Dict[str, Any]:
        """Create a new GitHub issue.

        Args:
            owner: The GitHub organization or user name
            repo: The repository name
            title: The issue title
            body: The issue body (optional)

        Returns:
            The created issue data

        Raises:
            GitHubClientError: If repository is not found or issue creation fails.
        """
        query = """
        mutation CreateIssue($repositoryId: ID!, $title: String!, $body: String) {
          createIssue(input: {
            repositoryId: $repositoryId,
            title: $title,
            body: $body
          }) {
            issue {
              id
              number
              title
              url
              state
            }
          }
        }
        """

        # First get the repository ID
        repo_query = """
        query GetRepositoryId($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id
          }
        }
        """

        repo_variables = {"owner": owner, "name": repo}

        try:
            repo_result = await self.execute_query(repo_query, repo_variables)
            if not repo_result.get("repository"):
                raise GitHubClientError(f"Repository {owner}/{repo} not found")
        except GitHubClientError as e:
            logger.error(f"Failed to get repository ID for {owner}/{repo}: {e}")
            raise

        repository_id = repo_result["repository"]["id"]

        variables = {"repositoryId": repository_id, "title": title, "body": body}

        try:
            result = await self.execute_query(query, variables)
            if not result.get("createIssue") or not result["createIssue"].get("issue"):
                raise GitHubClientError(f"Failed to create issue in {owner}/{repo}")
            return result["createIssue"]["issue"]
        except GitHubClientError as e:
            logger.error(f"Failed to create issue in {owner}/{repo}: {e}")
            raise
    
    async def update_issue_status(
        self, owner: str, project_number: int, issue_number: int, new_status_name: str
    ):
        """
        Updates the status of a GitHub issue within a ProjectV2.

        Args:
            owner (str): The username of the owner of the repository/project.
            project_number (int): The number of the project.
            issue_number (int): The number of the issue to update.
            new_status_name (str): The name of the desired status (e.g., "To Do", "In Progress").

        Returns:
            bool: True if the update was successful, False otherwise.
        """

        GET_PROJECT_DATA_QUERY = """
        query getProject($owner: String!, $project_number: Int!) {
          user(login: $owner) {
            projectV2(number: $project_number) {
              id # Project ID

              # Find the 'Status' field and its possible options
              field(name: "Status") {
                ... on ProjectV2SingleSelectField {
                  id # Status Field ID
                  options {
                    id # Option ID for each status
                    name # Name of the status (e.g., "To Do", "In Progress")
                  }
                }
              }

              # Get all items in the project to find the one we want to update
              items(first: 100) {
                nodes {
                  id # Item ID
                  content {
                    ... on Issue {
                      number # Issue number
                    }
                    ... on PullRequest {
                      number # PR number
                    }
                  }
                }
              }
            }
          }
        }
        """

        # This mutation updates the value of a specific field (in this case, "Status") for a specific item.
        UPDATE_ITEM_STATUS_MUTATION = """
        mutation updateStatus($project_id: ID!, $item_id: ID!, $status_field_id: ID!, $status_option_id: String!) {
          updateProjectV2ItemFieldValue(
            input: {
              projectId: $project_id
              itemId: $item_id
              fieldId: $status_field_id
              value: { 
                singleSelectOptionId: $status_option_id
              }
            }
          ) {
            projectV2Item {
              id
            }
          }
        }
        """

        # --- Step 1: Fetch all necessary IDs ---
        logger.debug("Step 1: Fetching project and item data...")
        variables = {"owner": owner, "project_number": project_number}

        try:
            response_data = await self.execute_query(GET_PROJECT_DATA_QUERY, variables)
        except GitHubClientError as e:
            logger.error(
                f"Failed to fetch project data for project number {project_number}: {e}"
            )
            raise

        try:
            project_data = response_data["user"]["projectV2"]
        except (KeyError, TypeError) as e:
            raise GitHubClientError(f"Error parsing GraphQL response: {e}")
        
        project_id = project_data.get("id")
        status_field = project_data.get("field")
        items = project_data.get("items", {}).get("nodes", [])

        if not all([project_id, status_field, items]):
            raise GitHubClientError("Could not find project_id, status_field or items")

        status_field_id = status_field.get("id")
        status_options = status_field.get("options", [])

        # Find the ID for the desired status name
        target_status_option_id = None
        for option in status_options:
            if option["name"].lower() == new_status_name.lower():
                target_status_option_id = option["id"]
                break

        if not target_status_option_id:
            available_statuses = [opt["name"] for opt in status_options]
            raise GitHubClientError(
                f"Error: Status '{new_status_name}' not found."
                f"Available statuses are: {', '.join(available_statuses)}"
            )

        # Find the item ID for the target issue number
        target_item_id = None
        for item in items:
            if item.get("content") and item["content"].get("number") == issue_number:
                target_item_id = item["id"]
                break

        if not target_item_id:
            raise GitHubClientError(
                f"Error: Issue #{issue_number} not found in project #{project_number}."
            )

        logger.debug("Successfully found all required IDs.")
        logger.debug(f"  - Project ID: {project_id}")
        logger.debug(f"  - Item (Issue) ID: {target_item_id}")
        logger.debug(f"  - Status Field ID: {status_field_id}")
        logger.debug(f"  - '{new_status_name}' Option ID: {target_status_option_id}")

        # --- Step 2: Execute the update mutation ---
        logger.debug("\nStep 2: Updating issue status...")
        mutation_variables = {
            "project_id": project_id,
            "item_id": target_item_id,
            "status_field_id": status_field_id,
            "status_option_id": target_status_option_id,
        }

        try:
            await self.execute_query(
                UPDATE_ITEM_STATUS_MUTATION, mutation_variables
            )
        except GitHubClientError as e:
            logger.error(f"Failed to update issue with id {target_item_id}: {e}")
            raise

        return True

    async def add_issue_to_project(
        self,
        owner: str,
        project_number: int,
        issue_owner: str,
        issue_repo: str,
        issue_number: int,
    ) -> Dict[str, Any]:
        """Add an existing GitHub issue to a Project V2.

        Args:
            owner: The GitHub organization or user name that owns the project
            project_number: The project number
            issue_owner: The owner of the repository containing the issue
            issue_repo: The repository name containing the issue
            issue_number: The issue number

        Returns:
            The project item data

        Raises:
            GitHubClientError: If project or issue is not found, or adding fails.
        """
        # Get project ID
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot add issue: {e}")
            raise

        # Get issue ID
        issue_query = """
        query GetIssueId($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              id
            }
          }
        }
        """

        issue_variables = {
            "owner": issue_owner,
            "repo": issue_repo,
            "number": issue_number,
        }

        try:
            issue_result = await self.execute_query(issue_query, issue_variables)
            if not issue_result.get("repository") or not issue_result["repository"].get(
                "issue"
            ):
                raise GitHubClientError(
                    f"Issue {issue_number} not found in {issue_owner}/{issue_repo}"
                )
        except GitHubClientError as e:
            logger.error(
                f"Failed to get issue ID for {issue_owner}/{issue_repo}#{issue_number}: {e}"
            )
            raise

        issue_id = issue_result["repository"]["issue"]["id"]

        # Add issue to project
        add_query = """
        mutation AddItemToProject($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {
            projectId: $projectId,
            contentId: $contentId
          }) {
            item {
              id
              content {
                ... on Issue {
                  title
                  number
                }
                ... on PullRequest {
                  title
                  number
                }
              }
            }
          }
        }
        """

        variables = {"projectId": project_id, "contentId": issue_id}

        try:
            result = await self.execute_query(add_query, variables)
            if not result.get("addProjectV2ItemById") or not result[
                "addProjectV2ItemById"
            ].get("item"):
                raise GitHubClientError(
                    f"Failed to add issue {issue_number} to project {project_number}"
                )
            return result["addProjectV2ItemById"]["item"]
        except GitHubClientError as e:
            logger.error(
                f"Failed to add issue {issue_number} to project {project_number}: {e}"
            )
            raise

    async def add_draft_issue_to_project(
        self, owner: str, project_number: int, title: str, body: str = ""
    ) -> Dict[str, Any]:
        """Add a draft issue to a GitHub Project V2.

        Args:
            owner: The GitHub organization or user name that owns the project
            project_number: The project number
            title: The draft issue title
            body: The draft issue body (optional)

        Returns:
            The project item data

        Raises:
            GitHubClientError: If project not found or adding fails.
        """
        # Get project ID
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot add draft issue: {e}")
            raise

        # Add draft issue to project
        add_query = """
        mutation AddDraftIssueToProject($projectId: ID!, $title: String!, $body: String) {
          addProjectV2DraftIssue(input: {
            projectId: $projectId,
            title: $title,
            body: $body
          }) {
            projectItem {
              id
            }
          }
        }
        """

        variables = {"projectId": project_id, "title": title, "body": body}

        try:
            result = await self.execute_query(add_query, variables)
            if not result.get("addProjectV2DraftIssue") or not result[
                "addProjectV2DraftIssue"
            ].get("projectItem"):
                raise GitHubClientError(
                    f"Failed to add draft issue to project {project_number}"
                )
            return result["addProjectV2DraftIssue"]["projectItem"]
        except GitHubClientError as e:
            logger.error(f"Failed to add draft issue to project {project_number}: {e}")
            raise

    async def update_project_item_field(
        self,
        owner: str,
        project_number: int,
        item_id: str,
        field_id: str,
        value: Any,  # Value type depends on the field
    ) -> Dict[str, Any]:
        """Update a field value for an item in a GitHub Project V2.

        Args:
            owner: The GitHub organization or user name that owns the project
            project_number: The project number
            item_id: The project item ID
            field_id: The field ID to update
            value: The new value (type depends on field: string, number, date, boolean, iteration ID, single select option ID)

        Returns:
            The updated project item data (containing the item ID)

        Raises:
            GitHubClientError: If project not found or update fails.
        """
        # Get project ID
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot update item field: {e}")
            raise
    
        field_value_input: Dict[str, Any] = {}
        if type(value) is float or type(value) is int:
            field_value_input = {"number": value}
        else:
            field_value_input = {"text": value}

        # Update field value
        update_query = """
        mutation UpdateProjectFieldValue($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId,
            itemId: $itemId,
            fieldId: $fieldId,
            value: $value
          }) {
            projectV2Item {
              id
            }
          }
        }
        """

        variables = {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "value": field_value_input,
        }

        try:
            result = await self.execute_query(update_query, variables)
            if not result.get("updateProjectV2ItemFieldValue") or not result[
                "updateProjectV2ItemFieldValue"
            ].get("projectV2Item"):
                raise GitHubClientError(
                    f"Failed to update field value for item {item_id}"
                )
            return result["updateProjectV2ItemFieldValue"]["projectV2Item"]
        except GitHubClientError as e:
            logger.error(f"Failed to update field {field_id} for item {item_id}: {e}")
            raise

    async def delete_project_item(
        self, owner: str, project_number: int, item_id: str
    ) -> str:
        """Delete an item from a GitHub Project V2.

        Args:
            owner: The GitHub organization or user name that owns the project
            project_number: The project number
            item_id: The project item ID

        Returns:
            The ID of the deleted item.

        Raises:
            GitHubClientError: If project not found or deletion fails.
        """
        # Get project ID
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot delete item: {e}")
            raise

        # Delete item
        delete_query = """
        mutation DeleteProjectItem($projectId: ID!, $itemId: ID!) {
          deleteProjectV2Item(input: {
            projectId: $projectId,
            itemId: $itemId
          }) {
            deletedItemId
          }
        }
        """

        variables = {"projectId": project_id, "itemId": item_id}

        try:
            result = await self.execute_query(delete_query, variables)
            if not result.get("deleteProjectV2Item") or not result[
                "deleteProjectV2Item"
            ].get("deletedItemId"):
                raise GitHubClientError(f"Failed to delete item {item_id}")
            return result["deleteProjectV2Item"]["deletedItemId"]
        except GitHubClientError as e:
            logger.error(f"Failed to delete item {item_id}: {e}")
            raise

    async def update_project_settings(
        self,
        owner: str,
        project_number: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        public: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update GitHub Project V2 settings.

        Args:
            owner: The GitHub organization or user name that owns the project
            project_number: The project number
            title: New project title (optional)
            description: New project description (optional)
            public: Whether the project should be public (optional)

        Returns:
            The updated project data

        Raises:
            GitHubClientError: If project not found or update fails.
        """
        # Get project ID
        try:
            project_id = await self.get_project_node_id(owner, project_number)
        except GitHubClientError as e:
            logger.error(f"Cannot update project settings: {e}")
            raise

        # Build input parameters
        input_params: Dict[str, Any] = {"projectId": project_id}  # Use Dict[str, Any]

        if title is not None:
            input_params["title"] = title

        if description is not None:
            input_params["shortDescription"] = description

        if public is not None:
            input_params["public"] = public  # Keep as boolean

        # Update project
        update_query = """
        mutation UpdateProject($input: UpdateProjectV2Input!) {
          updateProjectV2(input: $input) {
            projectV2 {
              id
              title
              shortDescription
              public
              url
            }
          }
        }
        """

        variables = {"input": input_params}

        try:
            result = await self.execute_query(update_query, variables)
            if not result.get("updateProjectV2") or not result["updateProjectV2"].get(
                "projectV2"
            ):
                raise GitHubClientError(f"Failed to update project {project_number}")
            return result["updateProjectV2"]["projectV2"]
        except GitHubClientError as e:
            logger.error(f"Failed to update project {project_number}: {e}")
            raise
