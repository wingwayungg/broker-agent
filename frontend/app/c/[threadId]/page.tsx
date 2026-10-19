import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Chat } from "@/components/Chat";
import { UUID_RE, getThread } from "@/lib/server";
import { conversationTitle } from "@/lib/title";

type Params = { params: Promise<{ threadId: string }> };

// Rendered on the server from the thread's checkpoint, so a shared or reloaded
// conversation arrives as HTML with its history, then hydrates. A run still in
// progress is re-joined by the client.
export default async function ThreadPage({ params }: Params) {
    const { threadId } = await params;
    const thread = UUID_RE.test(threadId) ? await getThread(threadId) : null;
    if (!thread) redirect("/");
    return <Chat initial={thread} />;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
    const { threadId } = await params;
    const thread = UUID_RE.test(threadId) ? await getThread(threadId) : null;
    const first = thread?.messages.find((m) => m.role === "user")?.content;
    return first ? { title: conversationTitle(first) } : {};
}
