import os
import sys
import requests
import uvicorn
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool
# --- CONFIGURATION ---
YOUTRACK_URL = os.getenv("YOUTRACK_URL", "https://votre-instance.youtrack.cloud").rstrip("/")
YOUTRACK_TOKEN = os.getenv("YOUTRACK_TOKEN")
PROJECT_ID = os.getenv("PROJECT_ID", "0-1")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

server = Server("youtrack-agent")


def _headers():
    return {
        "Authorization": f"Bearer {YOUTRACK_TOKEN}",
        "Content-Type": "application/json",
    }


def _create_subtask(headers: dict, parent_readable_id: str, parent_internal_id: str, task_title: str):
    payload_task = {
        "project": {"id": PROJECT_ID},
        "summary": task_title,
        "description": f"Subtask of {parent_readable_id}",
        "customFields": [
            {
                "name": "Type",
                "$type": "SingleEnumIssueCustomField",
                "value": {"name": "Subtask"},
            }
        ],
    }

    res_task = requests.post(
        f"{YOUTRACK_URL}/api/issues?fields=id,idReadable",
        headers=headers,
        json=payload_task,
        timeout=REQUEST_TIMEOUT,
    )
    if res_task.status_code not in (200, 201):
        return None

    task_data = res_task.json()
    task_internal_id = task_data.get("id")
    task_readable_id = task_data.get("idReadable")

    link_payload = {
        "direction": "OUTWARD",
        "linkType": {"name": "Subtask"},
        "issues": [{"id": task_internal_id}],
    }
    requests.post(
        f"{YOUTRACK_URL}/api/issues/{parent_internal_id}/links",
        headers=headers,
        json=link_payload,
        timeout=REQUEST_TIMEOUT,
    )

    return f"{task_readable_id}: {task_title}"


@server.list_tools()
async def handle_list_tools():
    return [
        Tool(
            name="create_youtrack_story",
            description="Cree une User Story (Milestone) et ses sous-taches (Subtask) dans YouTrack.",
            inputSchema={
                "type": "object",
                "properties": {
                    "story_title": {"type": "string", "description": "Titre de la User Story"},
                    "story_description": {"type": "string", "description": "Description detaillee"},
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des titres des sous-taches"
                    },
                },
                "required": ["story_title", "story_description", "tasks"],
            },
        ),
        Tool(
            name="update_youtrack_story",
            description="Met a jour une User Story existante et peut ajouter des sous-taches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "story_id": {
                        "type": "string",
                        "description": "ID de la User Story (ex: PRJ-123)",
                    },
                    "story_title": {
                        "type": "string",
                        "description": "Nouveau titre de la User Story",
                    },
                    "story_description": {
                        "type": "string",
                        "description": "Nouvelle description de la User Story",
                    },
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste de nouvelles sous-taches a ajouter",
                    },
                },
                "required": ["story_id"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if not YOUTRACK_TOKEN:
        return [{"type": "text", "text": "Erreur : YOUTRACK_TOKEN non configure."}]

    headers = _headers()

    if name == "create_youtrack_story":
        try:
            payload_story = {
                "project": {"id": PROJECT_ID},
                "summary": arguments["story_title"],
                "description": arguments["story_description"],
                "customFields": [
                    {
                        "name": "Type",
                        "$type": "SingleEnumIssueCustomField",
                        "value": {"name": "Milestone"},
                    }
                ],
            }
            res_story = requests.post(
                f"{YOUTRACK_URL}/api/issues?fields=id,idReadable",
                headers=headers,
                json=payload_story,
                timeout=REQUEST_TIMEOUT,
            )
            res_story.raise_for_status()

            story_data = res_story.json()
            s_id = story_data.get("idReadable")
            s_internal_id = story_data.get("id")

            created_tasks = []
            for t_title in arguments["tasks"]:
                task_label = _create_subtask(headers, s_id, s_internal_id, t_title)
                if task_label:
                    created_tasks.append(task_label)

            return [
                {
                    "type": "text",
                    "text": f"Story {s_id} creee!\nSous-taches:\n"
                    + "\n".join(f"  - {t}" for t in created_tasks),
                }
            ]
        except Exception as e:
            return [{"type": "text", "text": f"Erreur API YouTrack : {str(e)}"}]


    if name == "update_youtrack_story":
        try:
            story_id = arguments["story_id"]
            story_title = arguments.get("story_title")
            story_description = arguments.get("story_description")
            tasks = arguments.get("tasks", [])
            tasks_mode = arguments.get("tasks_mode", "add")

            if not story_title and not story_description and not tasks:
                return [
                    {
                        "type": "text",
                        "text": "Aucune modification demandee. Fournissez story_title, story_description ou tasks.",
                    }
                ]

            payload_update = {}
            if story_title:
                payload_update["summary"] = story_title
            if story_description:
                payload_update["description"] = story_description

            # Fetch story info and current subtasks
            story_res = requests.get(
                f"{YOUTRACK_URL}/api/issues/{story_id}?fields=id,idReadable,summary,links(direction,linkType(name),issues(id,idReadable,summary))",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if story_res.status_code == 404:
                return [{"type": "text", "text": f"Story introuvable: {story_id}"}]
            story_res.raise_for_status()

            story_data = story_res.json()
            s_internal_id = story_data.get("id")
            s_readable_id = story_data.get("idReadable", story_id)

            # Update title/description if needed
            if payload_update:
                update_res = requests.post(
                    f"{YOUTRACK_URL}/api/issues/{s_internal_id}?fields=id,idReadable,summary",
                    headers=headers,
                    json=payload_update,
                    timeout=REQUEST_TIMEOUT,
                )
                update_res.raise_for_status()
                story_data = update_res.json()
                s_internal_id = story_data.get("id", s_internal_id)
                s_readable_id = story_data.get("idReadable", s_readable_id)

            # Subtask management
            created_tasks = []
            removed_tasks = []
            subtask_links = [
                l for l in story_data.get("links", [])
                if l.get("linkType", {}).get("name") == "Subtask" and l.get("direction") == "OUTWARD"
            ]
            current_subtasks = []
            for link in subtask_links:
                for issue in link.get("issues", []):
                    current_subtasks.append({
                        "id": issue.get("id"),
                        "idReadable": issue.get("idReadable"),
                        "summary": issue.get("summary", "")
                    })

            if tasks_mode == "replace":
                # Remove all current subtasks
                for sub in current_subtasks:
                    res = requests.delete(
                        f"{YOUTRACK_URL}/api/issues/{s_internal_id}/links/Subtask/{sub['id']}",
                        headers=headers,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if res.status_code in (200, 204):
                        removed_tasks.append(sub["idReadable"])
                # Add new ones
                for t_title in tasks:
                    task_label = _create_subtask(headers, s_readable_id, s_internal_id, t_title)
                    if task_label:
                        created_tasks.append(task_label)
            elif tasks_mode == "remove":
                # Remove only those matching the provided titles
                for sub in current_subtasks:
                    if sub["summary"] in tasks:
                        res = requests.delete(
                            f"{YOUTRACK_URL}/api/issues/{s_internal_id}/links/Subtask/{sub['id']}",
                            headers=headers,
                            timeout=REQUEST_TIMEOUT,
                        )
                        if res.status_code in (200, 204):
                            removed_tasks.append(sub["idReadable"])
            else:  # add (default)
                for t_title in tasks:
                    task_label = _create_subtask(headers, s_readable_id, s_internal_id, t_title)
                    if task_label:
                        created_tasks.append(task_label)

            msg = f"Story {s_readable_id} mise a jour!\n"
            if tasks_mode == "replace":
                msg += f"Sous-taches remplacees. Nouvelles:\n" + ("\n".join(f"  - {t}" for t in created_tasks) if created_tasks else "  - Aucune")
            elif tasks_mode == "remove":
                msg += f"Sous-taches supprimees:\n" + ("\n".join(f"  - {t}" for t in removed_tasks) if removed_tasks else "  - Aucune")
            else:
                msg += f"Nouvelles sous-taches ajoutees:\n" + ("\n".join(f"  - {t}" for t in created_tasks) if created_tasks else "  - Aucune")
            return [
                {
                    "type": "text",
                    "text": msg,
                }
            ]
        except Exception as e:
            return [{"type": "text", "text": f"Erreur API YouTrack : {str(e)}"}]

    return [{"type": "text", "text": f"Outil inconnu: {name}"}]
async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
async def run_sse(port: int):
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    sse_transport = SseServerTransport("/messages")
    async def handle_sse(scope, receive, send):
        async with sse_transport.connect_sse(scope, receive, send) as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    async def handle_messages(scope, receive, send):
        await sse_transport.handle_post_message(scope, receive, send)
    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"])
    ])
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    await uv_server.serve()
if __name__ == "__main__":
    if "--sse" in sys.argv:
        port = int(os.getenv("PORT", 8000))
        print(f"MCP SSE mode (port {port})", file=sys.stderr)
        asyncio.run(run_sse(port))
    else:
        try:
            asyncio.run(run_stdio())
        except (KeyboardInterrupt, SystemExit):
            pass
