"use client";

import { useRouter } from "next/navigation";

// The API couldn't be reached or failed while reading the thread.
export default function ThreadError({ reset }: { error: Error; reset: () => void }) {
    const router = useRouter();
    return (
        <div id="log" className="empty">
            <div id="log-inner">
                <div className="empty-hero">
                    <h2>Couldn&apos;t load this conversation</h2>
                    <p>The assistant may be waking up. Try again in a moment.</p>
                    <div className="suggestions">
                        <button type="button" onClick={reset}>
                            Try again
                        </button>
                        <button type="button" onClick={() => router.push("/")}>
                            Start a new conversation
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
