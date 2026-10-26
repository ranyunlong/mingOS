package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ControlledComposition
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.snapshots.ObserverHandle
import androidx.compose.runtime.snapshots.Snapshot
import kotlin.coroutines.EmptyCoroutineContext

/**
 * A single-threaded, synchronous Compose session for Ming's bounded node tree.
 *
 * State writes are observed automatically. The input loop calls [recompose] to apply one turn of
 * pending work before rendering. Writes from SideEffect are observed on the following turn.
 * This bridge does not run an asynchronous recomposer loop or provide a platform frame clock;
 * LaunchedEffect, delay and animation scheduling are not supported by this contract.
 */
public class MingComposition() {
    internal val root: MingNode = MingNode(NodeKind.Root)
    private val applier = MingApplier(root)
    private val recomposer = Recomposer(EmptyCoroutineContext)
    private val composition = ControlledComposition(applier, recomposer)
    private val modified = mutableSetOf<Any>()
    private var updating = false
    private var observer: ObserverHandle? = Snapshot.registerApplyObserver { changed, _ ->
        modified.addAll(changed)
    }

    private var disposed = false
    public val isDisposed: Boolean get() = disposed

    // Internal lifecycle evidence for the same-module Wasm contract, never exposed to applications.
    internal val hasSnapshotObserver: Boolean get() = observer != null
    internal val hasPendingSnapshotChanges: Boolean get() = modified.isNotEmpty()

    public fun setContent(content: @Composable () -> Unit) {
        update {
            deliverModifications()
            // Recomposer.composeInitial owns the initial read/write snapshot and both apply phases.
            composition.setContent(content)
            applier.checkCommittedTree()
            composition.changesApplied()
        }
    }

    /** Returns true when this turn applies composition changes; a disposed session rejects calls. */
    public fun recompose(): Boolean = update {
        deliverModifications()
        if (!composition.hasInvalidations) return@update false
        val snapshot = Snapshot.takeMutableSnapshot(
            readObserver = { composition.recordReadOf(it) },
            writeObserver = { composition.recordWriteOf(it) },
        )
        val changed = try {
            val result = snapshot.enter { composition.recompose() }
            snapshot.apply().check()
            result
        } finally {
            snapshot.dispose()
        }
        if (changed) {
            composition.applyChanges()
            composition.applyLateChanges()
            applier.checkCommittedTree()
            composition.changesApplied()
            Snapshot.notifyObjectsInitialized()
        }
        changed
    }

    public fun dispose() {
        if (isDisposed) return
        check(!composition.isComposing) { "Cannot dispose MingComposition while composing" }
        disposed = true
        // Detach before disposal callbacks can write state or throw.
        try {
            observer?.dispose()
        } finally {
            observer = null
            modified.clear()
            try {
                composition.dispose()
            } finally {
                try {
                    recomposer.cancel()
                } finally {
                    // Also clear a partially applied tree when Runtime disposal itself fails.
                    applier.clear()
                }
            }
        }
    }

    private fun deliverModifications() {
        Snapshot.sendApplyNotifications()
        if (modified.isNotEmpty()) {
            val changes = modified.toSet()
            modified.clear()
            composition.recordModificationsOf(changes)
        }
    }

    private inline fun <T> update(block: () -> T): T {
        check(!isDisposed) { "MingComposition is disposed" }
        check(!updating) { "MingComposition update is already in progress" }
        updating = true
        try {
            return block()
        } catch (failure: Throwable) {
            try {
                dispose()
            } catch (cleanup: Throwable) {
                if (cleanup !== failure) failure.addSuppressed(cleanup)
            }
            throw failure
        } finally {
            updating = false
        }
    }
}

