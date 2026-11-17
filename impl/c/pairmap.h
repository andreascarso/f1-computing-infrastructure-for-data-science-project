/* Hash map from an id pair (e.g. raceId, driverId) to a row index.
 *
 * Open addressing with linear probing: one flat array of slots, no
 * per-entry malloc. The capacity is a power of two at least twice the
 * number of entries, so the table stays at most half full and a lookup
 * touches only a few slots on average. Used for the joins that pandas does
 * with merge(on=["raceId", "driverId"]).
 */
#ifndef F1_PAIRMAP_H
#define F1_PAIRMAP_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct {
    uint64_t *keys;
    long *values;      /* -1 = empty slot */
    size_t mask;       /* capacity - 1 */
} PairMap;

static inline uint64_t pair_key(long a, long b)
{
    return ((uint64_t)(uint32_t)a << 32) | (uint32_t)b;
}

static inline size_t pair_slot(const PairMap *m, uint64_t key)
{
    /* Multiplicative (Fibonacci) hashing spreads consecutive ids */
    return (size_t)((key * 0x9E3779B97F4A7C15ull) >> 20) & m->mask;
}

static inline PairMap pairmap_new(size_t entries)
{
    size_t cap = 16;
    while (cap < 2 * entries)
        cap *= 2;
    PairMap m = {malloc(cap * sizeof(uint64_t)), malloc(cap * sizeof(long)), cap - 1};
    if (!m.keys || !m.values) {
        fprintf(stderr, "out of memory\n");
        exit(1);
    }
    for (size_t i = 0; i < cap; i++)
        m.values[i] = -1;
    return m;
}

/* Insert (a, b) -> value. Returns 0, or 1 if the pair was already there
 * (the old value is kept: the caller decides whether that is an error). */
static inline int pairmap_put(PairMap *m, long a, long b, long value)
{
    uint64_t key = pair_key(a, b);
    size_t s = pair_slot(m, key);
    while (m->values[s] != -1) {
        if (m->keys[s] == key)
            return 1;
        s = (s + 1) & m->mask;
    }
    m->keys[s] = key;
    m->values[s] = value;
    return 0;
}

/* Value for (a, b), or -1 when the pair is not in the map. */
static inline long pairmap_get(const PairMap *m, long a, long b)
{
    uint64_t key = pair_key(a, b);
    size_t s = pair_slot(m, key);
    while (m->values[s] != -1) {
        if (m->keys[s] == key)
            return m->values[s];
        s = (s + 1) & m->mask;
    }
    return -1;
}

static inline void pairmap_free(PairMap *m)
{
    free(m->keys);
    free(m->values);
}

#endif
