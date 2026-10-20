export const APP_TITLE = "Broker Portfolio Agent";

// A conversation is titled by its first message, in the server-rendered
// metadata and when the browser starts one.
export function conversationTitle(firstMessage: string) {
    return firstMessage.length > 60 ? `${firstMessage.slice(0, 57)}...` : firstMessage;
}
