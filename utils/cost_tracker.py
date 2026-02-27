"""Cost tracking and budget enforcement for API calls."""

from rich.console import Console
from rich.table import Table
from config import settings
from utils.database import get_total_spend, log_cost, get_connection

console = Console()


class CostTracker:
    """Tracks API costs and enforces budget limits."""
    
    def __init__(self):
        self.session_cost = 0.0
    
    @property
    def total_spend(self) -> float:
        """Total spend across all sessions."""
        return get_total_spend()
    
    @property
    def remaining_budget(self) -> float:
        """Remaining budget for this run."""
        return settings.COST_LIMIT - self.session_cost
    
    def can_afford(self, engine: str, quality: str) -> bool:
        """Check if we can afford to generate another video."""
        estimated_cost = settings.get_cost_per_video(engine, quality)
        return self.remaining_budget >= estimated_cost
    
    def record_cost(self, vehicle_id: int, engine: str, quality: str, 
                    duration: float, cost: float, call_type: str = "video_generation"):
        """Record an API cost."""
        self.session_cost += cost
        log_cost(vehicle_id, engine, quality, duration, cost, call_type)
    
    def get_best_engine(self) -> tuple[str, str]:
        """Determine the best engine/quality combo within budget."""
        primary = settings.PRIMARY_VIDEO_ENGINE
        quality = settings.VIDEO_QUALITY
        
        if self.can_afford(primary, quality):
            return primary, quality
        
        # Try cheaper alternatives
        fallbacks = [
            ("sora", "fast"),
            ("veo", "fast"),
        ]
        
        for engine, q in fallbacks:
            if self.can_afford(engine, q):
                return engine, q
        
        return None, None  # Can't afford anything
    
    def print_summary(self):
        """Print a cost summary table."""
        conn = get_connection()
        
        table = Table(title="💰 Cost Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")
        
        table.add_row("Session Spend", f"${self.session_cost:.2f}")
        table.add_row("All-Time Spend", f"${self.total_spend:.2f}")
        table.add_row("Budget Limit", f"${settings.COST_LIMIT:.2f}")
        table.add_row("Remaining", f"${self.remaining_budget:.2f}")
        
        # Breakdown by engine
        cursor = conn.execute(
            "SELECT engine, COUNT(*) as count, SUM(cost) as total "
            "FROM cost_log WHERE api_call_type = 'video_generation' GROUP BY engine"
        )
        for row in cursor.fetchall():
            table.add_row(
                f"  {row['engine'].upper()} Videos",
                f"{row['count']} (${row['total']:.2f})"
            )
        
        conn.close()
        console.print(table)
