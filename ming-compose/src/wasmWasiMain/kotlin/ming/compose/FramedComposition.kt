package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Composition
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.snapshots.Snapshot
import kotlinx.coroutines.launch

/** The official asynchronous Recomposer owns recomposition and both apply phases. */
internal class FramedComposition(initialNanos: Long, nowProvider: () -> Long) {
    internal val root = MingNode(NodeKind.Root)
    private val applier = MingApplier(root)
    private val dispatcher = FrameDispatcher(initialNanos, nowProvider)
    private val clock = HostFrameClock(dispatcher)
    private val recomposer = Recomposer(dispatcher.context)
    // The parent clock belongs to the runner coroutine, not only the effect constructor context.
    private val runner = dispatcher.scope.launch(clock) { recomposer.runRecomposeAndApplyChanges() }
    private val composition = Composition(applier, recomposer)
    var isDisposed = false
        private set
    val wantsFrame: Boolean get() = !isDisposed && (clock.pending > 0 || recomposer.hasPendingWork)
    val nextDeadlineNanos: Long? get() = dispatcher.nextDeadlineNanos
    val revision: Long get() = recomposer.changeCount

    init { pump() }

    fun setContent(content: @Composable () -> Unit) = guarded {
        composition.setContent(content)
        pumpWork()
    }
    fun pump() = guarded { pumpWork() }
    fun advance(nanos: Long, frame: Boolean) = guarded {
        dispatcher.advanceTo(nanos)
        pumpWork()
        if (frame) {
            clock.sendFrame(nanos)
            pumpWork()
        }
    }
    private fun pumpWork() {
        repeat(4) { dispatcher.drain(); Snapshot.sendApplyNotifications() }
        dispatcher.drain()
        // Never check per change batch: late changes may still remove replaced old nodes.
        applier.checkCommittedTree()
        dispatcher.throwIfFailed()
    }
    private inline fun <T> guarded(block: () -> T): T {
        check(!isDisposed) { "FRAMED_COMPOSITION_DISPOSED" }
        try { return block() } catch (failure: Throwable) {
            try { dispose() } catch (cleanup: Throwable) {
                if (cleanup !== failure) failure.addSuppressed(cleanup)
            }
            throw failure
        }
    }
    fun dispose() {
        if (isDisposed) return
        isDisposed = true
        var first: Throwable? = null
        fun clean(block: () -> Unit) {
            try { block() } catch (failure: Throwable) {
                val original = first
                if (original == null) first = failure else if (failure !== original) original.addSuppressed(failure)
            }
        }
        clean { composition.dispose() }
        clean { recomposer.cancel() }
        clean { runner.cancel() }
        clean { clock.close() }
        clean { dispatcher.close() }
        clean { applier.clear() }
        clean { dispatcher.throwIfFailed() }
        first?.let { throw it }
    }
}
