package qualification
import ming.sdk.*
fun main() {
    MingLog.event(800, MingFiles.length(1))
    MingInput.next()
    MingText.create("legacy", 16f)
    MingFrame3(10, 10).submit()
    MingFrame4(10, 10).submit()
    MingFrames.monotonicNanos()
    MingFrames.wait(false, nonblocking = true)
}
