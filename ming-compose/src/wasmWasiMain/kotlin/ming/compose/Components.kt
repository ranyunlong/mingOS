package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ComposeNode

@Composable
public fun MingColumn(padding: Int = 24, gap: Int = 12, content: @Composable () -> Unit) {
    require(padding in 0..64 && gap in 0..64) { "MingColumn padding and gap must be 0..64" }
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.Column) },
        update = {
            set(padding) { this.padding = it }
            set(gap) { this.gap = it }
        },
        content = content,
    )
}

@Composable
public fun MingText(value: String, emPx: Int = 18, color: Int = 0xff203b37.toInt()) {
    require(emPx in 8..64 && value.encodeToByteArray().size <= 512) { "Invalid MingText size or UTF-8 length" }
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.Text) },
        update = {
            set(value) { text = it }
            set(emPx) { this.emPx = it }
            set(color) { this.color = it }
        },
    )
}

@Composable
public fun MingButton(label: String, onClick: () -> Unit) {
    require(label.encodeToByteArray().size <= 512) { "MingButton label exceeds 512 UTF-8 bytes" }
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.Button) },
        update = {
            set(label) { text = it }
            set(onClick) { this.onClick = it }
            set(-1) { color = it }
        },
    )
}
