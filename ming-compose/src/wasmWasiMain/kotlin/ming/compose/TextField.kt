package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ComposeNode
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ming.sdk.TextEditState

/** A caller-owned Compose state; editing values remain immutable and own no Host resources. */
public class TextFieldState(initial: TextEditState = TextEditState()) {
    public var value: TextEditState by mutableStateOf(initial)
    public var isFocused: Boolean by mutableStateOf(false)
        internal set
}

@Composable
public fun rememberTextFieldState(initialText: String = ""): TextFieldState =
    remember { TextFieldState(TextEditState(initialText)) }

/** Single-line scalar-safe field. Input/focus requires MingTextApplication, not the legacy entries. */
@Composable
public fun MingTextField(
    state: TextFieldState,
    placeholder: String = "",
    emPx: Int = 18,
    color: Int = 0xff203b37.toInt(),
) {
    require(emPx in 8..64 && placeholder.encodeToByteArray().size <= 512) { "Invalid field style/placeholder" }
    // Validate scalar text even when the placeholder is not currently displayed.
    TextEditState(placeholder)
    val value = state.value
    val focused = state.isFocused
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.TextField) },
        update = {
            set(state) { fieldState = it }
            set(value) { fieldValue = it }
            set(focused) { fieldFocused = it }
            set(placeholder) { text = it }
            set(emPx) { this.emPx = it }
            set(color) { this.color = it }
        },
    )
}
