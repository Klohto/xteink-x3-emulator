/* Opt-in host gateway routing for unmodified stock firmware endpoint tests.
 * Only socket calls whose immediate caller is the dynamically linked libslirp
 * are eligible. No guest memory, ESP-IDF API, packet payload, certificate,
 * firmware URL or native device state is changed.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

enum { DNS_ROUTE, NTP_ROUTE, HTTP_ROUTE, TLS_ROUTE, ROUTE_COUNT, MAX_FDS = 65536 };
static unsigned route_ports[ROUTE_COUNT];
static unsigned long calls[ROUTE_COUNT], restored, failures;
static bool enabled;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct { bool set; struct sockaddr_in original, routed; } peers[MAX_FDS];
static int (*next_connect)(int, const struct sockaddr *, socklen_t);
static ssize_t (*next_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
static ssize_t (*next_recvfrom)(int, void *, size_t, int, struct sockaddr *, socklen_t *);
static int (*next_close)(int);

static bool slirp_caller(void *pc)
{
    Dl_info information;
    if (!enabled || !dladdr(pc, &information) || !information.dli_fname) {
        return false;
    }
    const char *base = strrchr(information.dli_fname, '/');
    base = base ? base + 1 : information.dli_fname;
    return !strncmp(base, "libslirp.so", strlen("libslirp.so"));
}

static unsigned parse_port(const char *name)
{
    const char *value = getenv(name);
    if (!value || !*value) { return 0; }
    char *end;
    errno = 0;
    unsigned long port = strtoul(value, &end, 10);
    if (errno || *end || port < 1024 || port > 65535) {
        fprintf(stderr, "x3emu-host-router: invalid %s (expected unprivileged port)\n", name);
        return UINT32_MAX;
    }
    return (unsigned)port;
}

__attribute__((constructor)) static void initialize(void)
{
    next_connect = dlsym(RTLD_NEXT, "connect");
    next_sendto = dlsym(RTLD_NEXT, "sendto");
    next_recvfrom = dlsym(RTLD_NEXT, "recvfrom");
    next_close = dlsym(RTLD_NEXT, "close");
    const char *active = getenv("X3EMU_HOST_ROUTER");
    if (!active || strcmp(active, "1")) { return; }
    const char *names[] = { "X3EMU_ROUTE_DNS_PORT", "X3EMU_ROUTE_NTP_PORT",
                           "X3EMU_ROUTE_HTTP_PORT", "X3EMU_ROUTE_TLS_PORT" };
    for (unsigned index = 0; index < ROUTE_COUNT; ++index) {
        route_ports[index] = parse_port(names[index]);
        if (route_ports[index] == UINT32_MAX) { return; }
    }
    enabled = next_connect && next_sendto && next_recvfrom && next_close;
    fprintf(stderr, "x3emu-host-router: {\"active\":%s,\"scope\":\"libslirp-host-egress\","
            "\"dns_original\":\"system-resolver:53\",\"loopback_original_ports\":[123,80,443],"
            "\"translated_address\":\"127.0.0.1\",\"translated_ports\":[%u,%u,%u,%u]}\n",
            enabled ? "true" : "false", route_ports[0], route_ports[1], route_ports[2], route_ports[3]);
}

static int translation(int fd, const struct sockaddr *address, socklen_t length,
                       struct sockaddr_in *result)
{
    if (fd < 0 || fd >= MAX_FDS || length < sizeof(*result) || !address || address->sa_family != AF_INET) {
        return -1;
    }
    const struct sockaddr_in *original = (const struct sockaddr_in *)address;
    unsigned port = ntohs(original->sin_port);
    int route = -1;
    if (port == 53) {
        route = DNS_ROUTE;
    } else if (original->sin_addr.s_addr == htonl(INADDR_LOOPBACK)) {
        if (port == 123) { route = NTP_ROUTE; }
        if (port == 80) { route = HTTP_ROUTE; }
        if (port == 443) { route = TLS_ROUTE; }
    }
    if (route < 0 || !route_ports[route]) { return -1; }
    *result = *original;
    result->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    result->sin_port = htons(route_ports[route]);
    pthread_mutex_lock(&lock);
    peers[fd].set = true;
    peers[fd].original = *original;
    peers[fd].routed = *result;
    ++calls[route];
    pthread_mutex_unlock(&lock);
    return route;
}

int connect(int fd, const struct sockaddr *address, socklen_t length)
{
    struct sockaddr_in redirected;
    bool routed = slirp_caller(__builtin_return_address(0)) && translation(fd, address, length, &redirected) >= 0;
    if (!next_connect) { next_connect = dlsym(RTLD_NEXT, "connect"); }
    int result = next_connect(fd, routed ? (const struct sockaddr *)&redirected : address,
                              routed ? sizeof(redirected) : length);
    if (routed && result < 0 && errno != EINPROGRESS) {
        pthread_mutex_lock(&lock); ++failures; pthread_mutex_unlock(&lock);
    }
    return result;
}

ssize_t sendto(int fd, const void *buffer, size_t length, int flags,
               const struct sockaddr *address, socklen_t address_length)
{
    struct sockaddr_in redirected;
    bool routed = slirp_caller(__builtin_return_address(0)) && translation(fd, address, address_length, &redirected) >= 0;
    if (!next_sendto) { next_sendto = dlsym(RTLD_NEXT, "sendto"); }
    ssize_t result = next_sendto(fd, buffer, length, flags,
                               routed ? (const struct sockaddr *)&redirected : address,
                               routed ? sizeof(redirected) : address_length);
    if (routed && result < 0) { pthread_mutex_lock(&lock); ++failures; pthread_mutex_unlock(&lock); }
    return result;
}

ssize_t recvfrom(int fd, void *buffer, size_t length, int flags,
                 struct sockaddr *address, socklen_t *address_length)
{
    if (!next_recvfrom) { next_recvfrom = dlsym(RTLD_NEXT, "recvfrom"); }
    ssize_t result = next_recvfrom(fd, buffer, length, flags, address, address_length);
    if (result >= 0 && slirp_caller(__builtin_return_address(0)) && fd >= 0 && fd < MAX_FDS
        && address && address_length && *address_length >= sizeof(struct sockaddr_in) && address->sa_family == AF_INET) {
        struct sockaddr_in *received = (struct sockaddr_in *)address;
        pthread_mutex_lock(&lock);
        if (peers[fd].set && received->sin_addr.s_addr == peers[fd].routed.sin_addr.s_addr
            && received->sin_port == peers[fd].routed.sin_port) {
            *received = peers[fd].original;
            ++restored;
        }
        pthread_mutex_unlock(&lock);
    }
    return result;
}

int close(int fd)
{
    if (fd >= 0 && fd < MAX_FDS && enabled) {
        pthread_mutex_lock(&lock); peers[fd].set = false; pthread_mutex_unlock(&lock);
    }
    if (!next_close) { next_close = dlsym(RTLD_NEXT, "close"); }
    return next_close(fd);
}

__attribute__((destructor)) static void report(void)
{
    if (!enabled) { return; }
    fprintf(stderr, "x3emu-host-router: {\"finished\":true,\"dns_calls\":%lu,\"ntp_calls\":%lu,"
            "\"http_calls\":%lu,\"tls_calls\":%lu,\"udp_source_restorations\":%lu,\"errors\":%lu}\n",
            calls[0], calls[1], calls[2], calls[3], restored, failures);
}
