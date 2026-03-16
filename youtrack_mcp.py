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
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID") or os.getenv("knowledge_base_id") or PROJECT_ID
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
            description="Met a jour une User Story existante et peut ajouter des sous-taches. Permet aussi de changer le statut et l'assignation.",
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
                    "status": {
                        "type": "string",
                        "description": "Nouveau statut de la User Story (ex: 'In Progress')",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Nom d'utilisateur ou email de l'assigné (ex: 'john.doe' ou 'john.doe@email.com')",
                    },
                },
                "required": ["story_id"],
            },
        ),
        Tool(
            name="add_youtrack_documentation",
            description="Cree un article dans la base de connaissances YouTrack (Knowledge Base). Necessite l'ID interne du projet YouTrack ayant la Knowledge Base activee (ex: '0-1').",
            inputSchema={
                "type": "object",
                "properties": {
                    "doc_title": {"type": "string", "description": "Titre de la documentation"},
                    "doc_content": {"type": "string", "description": "Contenu de la documentation (Markdown supporte)"},
                    "knowledge_base_id": {"type": "string", "description": "ID interne du projet YouTrack avec Knowledge Base activee (ex: '0-1'). Recuperable via GET /api/projects?fields=id,shortName"},
                    "parent_article_id": {"type": "string", "description": "ID de l'article parent (optionnel, ex: '62-3')", "nullable": True},
                },
                "required": ["doc_title", "doc_content", "knowledge_base_id"],
            },
        ),
        Tool(
            name="delete_youtrack_issue",
            description="Supprime une issue YouTrack par son ID (ex: MSC-68).",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "ID lisible de l'issue (ex: MSC-68)"},
                },
                "required": ["issue_id"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if not YOUTRACK_TOKEN:
        return [{"type": "text", "text": "Erreur : YOUTRACK_TOKEN non configure."}]

    headers = _headers()

    if name == "delete_youtrack_issue":
        try:
            print(f"[DEBUG] Called delete_youtrack_issue for: {arguments.get('issue_id')}")
            issue_id = arguments["issue_id"]
            # Get internal ID from readable ID
            res = requests.get(
                f"{YOUTRACK_URL}/api/issues/{issue_id}?fields=id,idReadable",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if res.status_code == 404:
                print(f"[DEBUG] Issue not found: {issue_id}")
                return [{"type": "text", "text": f"Issue introuvable: {issue_id}"}]
            res.raise_for_status()
            internal_id = res.json().get("id")
            del_res = requests.delete(
                f"{YOUTRACK_URL}/api/issues/{internal_id}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if del_res.status_code in (200, 204):
                print(f"[DEBUG] Issue {issue_id} deleted successfully.")
                return [{"type": "text", "text": f"Issue {issue_id} supprimee."}]
            else:
                print(f"[DEBUG] Error deleting issue: {del_res.status_code} {del_res.text}")
                return [{"type": "text", "text": f"Erreur suppression: {del_res.status_code} {del_res.text}"}]
        except Exception as e:
            print(f"[DEBUG] Exception in delete_youtrack_issue: {str(e)}")
            return [{"type": "text", "text": f"Erreur API YouTrack (delete): {str(e)}"}]

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

    if name == "add_youtrack_documentation":
        try:
            kb_id = arguments.get("knowledge_base_id") or KNOWLEDGE_BASE_ID
            if not kb_id:
                print(f"[DEBUG] add_youtrack_documentation: missing knowledge_base_id and PROJECT_ID not set")
                return [{"type": "text", "text": "Erreur : knowledge_base_id requis (ID interne du projet YouTrack, ex: '0-1')."}]

            print(f"[DEBUG] add_youtrack_documentation: using knowledge_base_id={kb_id}")

            payload_kb = {
                "summary": arguments["doc_title"],
                "content": arguments["doc_content"],
                "project": {"id": kb_id},
            }
            parent_article_id = arguments.get("parent_article_id")
            if parent_article_id:
                payload_kb["parent"] = {"id": parent_article_id}

            print(f"[DEBUG] add_youtrack_documentation: payload_kb={payload_kb}")
            res_kb = requests.post(
                f"{YOUTRACK_URL}/api/articles?fields=id,summary,project(id,shortName),parent(id)",
                headers=headers,
                json=payload_kb,
                timeout=REQUEST_TIMEOUT,
            )
            res_kb.raise_for_status()
            kb_data = res_kb.json()
            article_id = kb_data.get("id")
            article_title = kb_data.get("summary")
            print(f"[DEBUG] add_youtrack_documentation: created article_id={article_id}, title={article_title}")
            return [
                {
                    "type": "text",
                    "text": f"Article KB '{article_title}' cree (ID: {article_id})!",
                }
            ]
        except Exception as e:
            print(f"[DEBUG] Exception in add_youtrack_documentation: {str(e)}")
            return [{"type": "text", "text": f"Erreur API YouTrack KB : {str(e)}"}]

    if name == "update_youtrack_story":

        try:
            story_id = arguments["story_id"]
            story_title = arguments.get("story_title")
            story_description = arguments.get("story_description")
            tasks = arguments.get("tasks", [])
            tasks_mode = arguments.get("tasks_mode", "add")
            status = arguments.get("status")
            assignee = arguments.get("assignee")

            if not (story_title or story_description or tasks or status or assignee):
                return [
                    {
                        "type": "text",
                        "text": "Aucune modification demandee. Fournissez story_title, story_description, status, assignee ou tasks.",
                    }
                ]

            payload_update = {}
            custom_fields = []
            if story_title:
                payload_update["summary"] = story_title
            if story_description:
                payload_update["description"] = story_description
            if status:
                custom_fields.append({
                    "name": "State",
                    "$type": "SingleEnumIssueCustomField",
                    "value": {"name": status}
                })
            if assignee:
                custom_fields.append({
                    "name": "Assignee",
                    "$type": "SingleUserIssueCustomField",
                    "value": {"login": assignee}
                })
            # Always send payload_update if any custom_fields or summary/description is present
            if custom_fields:
                payload_update["customFields"] = custom_fields

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

            # Update title/description/status/assignee if needed
            if story_title or story_description or status or assignee or tasks:
                if payload_update:
                    update_res = requests.post(
                        f"{YOUTRACK_URL}/api/issues/{s_internal_id}?fields=id,idReadable,summary,customFields(name,value(login,name))",
                        headers=headers,
                        json=payload_update,
                        timeout=REQUEST_TIMEOUT,
                    )
                    response_text = update_res.text
                    if update_res.status_code not in (200, 201):
                        return [{
                            "type": "text",
                            "text": f"Erreur API YouTrack : {update_res.status_code} {response_text}"
                        }]
                    # Always show the response for debugging
                    return [{
                        "type": "text",
                        "text": f"Réponse YouTrack : {response_text}"
                    }]

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
