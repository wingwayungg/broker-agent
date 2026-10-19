import { ChatShellProvider } from "@/components/ChatShell";
import { Composer } from "@/components/Composer";
import { Header } from "@/components/Header";

// Header and Composer live here rather than in <Chat>, so they are sent before
// the thread is read and stay mounted from loading.tsx to the page and across
// navigations; <Chat> wires them up once it mounts.
export default function ChatLayout({ children }: { children: React.ReactNode }) {
    return (
        <ChatShellProvider>
            <Header />
            <main>{children}</main>
            <Composer />
        </ChatShellProvider>
    );
}
