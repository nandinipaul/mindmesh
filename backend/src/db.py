import re
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
import time

DB_DIR = Path(__file__).resolve().parent.parent / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "mindmesh.db"
OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize SQLite tables for blueprints and history tracking."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blueprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                business_idea TEXT NOT NULL,
                technology_preference TEXT,
                cloud_preference TEXT,
                expected_daily_traffic TEXT,
                delivery_timeline_months INTEGER,
                data_hosting_country TEXT,
                markdown_content TEXT,
                html_content TEXT,
                status TEXT DEFAULT 'completed'
            )
        """)
        #new
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'approved',
            user_feedback TEXT,
            evaluator_feedback TEXT,
            created_at TEXT NOT NULL,
            is_current INTEGER DEFAULT 1
            )
        """)


        cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_id ON blueprints(run_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON blueprints(created_at DESC)")
        conn.commit()


from src.utils.html_converter import markdown_to_html


def sync_filesystem_blueprints():
    """
    Backfill and sync any existing output files from outputs/ into SQLite database.
    Extracts business idea, tech preferences, and timestamps directly from the markdown.
    """
    if not OUTPUTS_DIR.exists():
        return

    md_files = list(OUTPUTS_DIR.glob("*.md"))
    for md_file in md_files:
        run_id = md_file.stem
        if run_id == "final_output":
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
            html_content = markdown_to_html(content, title=f"MindMesh Blueprint - {run_id}")
            html_file = OUTPUTS_DIR / f"{run_id}.html"
            html_file.write_text(html_content, encoding="utf-8")

            # Extract business idea
            business_idea = ""
            m_idea = re.search(r"\*\*Business Idea:\*\*\s*\n*(.+?)(?=\n---|\n##|\n\n\n|$)", content, re.DOTALL)
            if m_idea:
                business_idea = m_idea.group(1).strip()
            
            if not business_idea:
                # Try finding project title or heading
                m_proj = re.search(r"\*\*Project:\*\*\s*(.+)", content)
                if m_proj:
                    business_idea = m_proj.group(1).strip()
                else:
                    m_head = re.search(r"### Business Analysis Report:\s*(.+)", content)
                    if m_head:
                        business_idea = m_head.group(1).strip()
                    else:
                        business_idea = f"Solution Architecture Blueprint ({run_id})"

            # Extract metadata
            m_cloud = re.search(r"\*\*Target Cloud:\*\*\s*`?([^`|\n]+)`?", content)
            cloud_pref = m_cloud.group(1).strip() if m_cloud else "Cloud Native"

            m_tech = re.search(r"\*\*Tech Stack:\*\*\s*`?([^`|\n]+)`?", content)
            tech_pref = m_tech.group(1).strip() if m_tech else "Modern Stack"

            m_time = re.search(r"\*\*Generation Timestamp:\*\*\s*`?([^`|\n]+)`?", content)
            created_at = m_time.group(1).strip() if m_time else time.strftime("%Y-%m-%d %H:%M:%S")

            save_blueprint_record(
                run_id=run_id,
                inputs={
                    "business_idea": business_idea,
                    "technology_preference": tech_pref,
                    "cloud_preference": cloud_pref,
                    "expected_daily_traffic": "",
                    "delivery_timeline_months": 6,
                    "data_hosting_country": ""
                },
                markdown_content=content,
                html_content=html_content,
                status="completed"
            )
        except Exception as e:
            print(f"Sync error for {md_file}: {e}")


def save_blueprint_record(
    run_id: str,
    inputs: Dict[str, Any],
    markdown_content: str,
    html_content: str,
    status: str = "completed"
) -> bool:
    """Insert or replace a blueprint record in SQLite."""
    init_db()
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO blueprints (
                run_id, created_at, business_idea, technology_preference,
                cloud_preference, expected_daily_traffic, delivery_timeline_months,
                data_hosting_country, markdown_content, html_content, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            created_at,
            inputs.get("business_idea", ""),
            inputs.get("technology_preference", ""),
            inputs.get("cloud_preference", ""),
            inputs.get("expected_daily_traffic", ""),
            int(inputs.get("delivery_timeline_months", 6)),
            inputs.get("data_hosting_country", ""),
            markdown_content,
            html_content,
            status
        ))
        conn.commit()
    return True

#new
def save_agent_output(
    run_id: str,
    agent_name: str,
    content: str,
    status: str = "approved",
    user_feedback: str = "",
    evaluator_feedback: str = "",
    ) -> bool:
    """Save an agent output and automatically assign its version."""

    init_db()

    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Find the latest version for this agent
        cursor.execute("""
            SELECT MAX(version)
            FROM agent_outputs
            WHERE run_id = ? AND agent_name = ?
        """, (run_id, agent_name))

        row = cursor.fetchone()

        latest_version = row[0] or 0
        new_version = latest_version + 1

        # Mark previous version as no longer current
        cursor.execute("""
            UPDATE agent_outputs
            SET is_current = 0
            WHERE run_id = ? AND agent_name = ?
        """, (run_id, agent_name))

        # Save the new version
        cursor.execute("""
            INSERT INTO agent_outputs (
                run_id,
                agent_name,
                version,
                content,
                status,
                user_feedback,
                evaluator_feedback,
                created_at,
                is_current
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            run_id,
            agent_name,
            new_version,
            content,
            status,
            user_feedback,
            evaluator_feedback,
            created_at
        ))

        conn.commit()

    return True


#new
def get_current_agent_outputs(run_id: str) -> List[Dict[str, Any]]:
    """Get the latest/current output for every agent in a blueprint run."""
    init_db()

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM agent_outputs
            WHERE run_id = ? AND is_current = 1
            ORDER BY id ASC
        """, (run_id,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_agent_output(
    run_id: str,
    agent_name: str
    ) -> Optional[Dict[str, Any]]:
    """Get the current output of a specific agent."""
    init_db()

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM agent_outputs
            WHERE run_id = ?
              AND agent_name = ?
              AND is_current = 1
            ORDER BY version DESC
            LIMIT 1
        """, (run_id, agent_name))

        row = cursor.fetchone()
        
        if row:
            return dict(row)

    return None
    


def get_blueprint_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve history of saved blueprints."""
    init_db()
    sync_filesystem_blueprints()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, run_id, created_at, business_idea, technology_preference,
                   cloud_preference, expected_daily_traffic, delivery_timeline_months,
                   data_hosting_country, status
            FROM blueprints
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_blueprint_by_run_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full blueprint record by run_id."""
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM blueprints WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def delete_blueprint_by_run_id(run_id: str) -> bool:
    """Delete blueprint record by run_id."""
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blueprints WHERE run_id = ?", (run_id,))
        conn.commit()
        return cursor.rowcount > 0


# Automatically ensure DB tables on import & backfill existing blueprints
init_db()
sync_filesystem_blueprints()

