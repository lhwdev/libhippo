"""Knowledge Subsystem Agent Manager.

Owns and reuses the singletons of framework knowledge agents and orchestrator:
CuratorAgent, CheckerAgent, VerifierAgent, BookKeeperAgent, and MakerCheckerOrchestrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from libhippo.agents.book_keeper import BookKeeperAgent
from libhippo.agents.checker import CheckerAgent
from libhippo.agents.curator import CuratorAgent
from libhippo.agents.verifier import VerifierAgent
from libhippo.orchestration.maker_checker import MakerCheckerOrchestrator
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


class AgentManager:
    """Manages lifecycle, coordination, and reuse of framework knowledge agents."""

    def __init__(
        self,
        store: KnowledgeStore,
        curator: CuratorAgent | None = None,
        checker: CheckerAgent | None = None,
        verifier: VerifierAgent | None = None,
        book_keeper: BookKeeperAgent | None = None,
        orchestrator: MakerCheckerOrchestrator | None = None,
    ) -> None:
        self.store = store
        self.curator = curator or CuratorAgent()
        self.checker = checker or CheckerAgent()
        self.verifier = verifier or VerifierAgent(store=store)
        self.book_keeper = book_keeper or BookKeeperAgent(store=store)
        self.orchestrator = orchestrator or MakerCheckerOrchestrator(
            store=store,
            checker=self.checker,
            curator=self.curator,
            verifier=self.verifier,
        )
        self._dispatcher: KnowledgeDispatcher | None = None

    def create_dispatcher(
        self,
        threshold_low: float = 0.70,
        threshold_medium: float = 0.82,
    ) -> KnowledgeDispatcher:
        """Create or return singleton KnowledgeDispatcher wired to this manager's agents and orchestrator."""
        if self._dispatcher is None:
            self._dispatcher = KnowledgeDispatcher(
                store=self.store,
                curator=self.curator,
                checker=self.checker,
                book_keeper=self.book_keeper,
                orchestrator=self.orchestrator,
                threshold_low=threshold_low,
                threshold_medium=threshold_medium,
            )
        return self._dispatcher
