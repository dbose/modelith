/** Client-side ULID minting — a FORMAT port of mdl_core/ids.py, nothing more.
 *
 * The modeler app stages edits and proposes them later, so a created object needs
 * an identity at the moment the user creates it: the entity they see on the canvas
 * must be the entity that lands in the pull request. Server-side minting cannot do
 * that, because preview and propose are two separate runs and would mint twice.
 *
 * This ports only the ENCODING (48-bit ms timestamp + 80 bits of entropy, Crockford
 * base32, lexicographically sortable). No mutation logic is duplicated — the
 * handlers still run server-side, and they validate any id we send.
 */

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

function encode(value: bigint, length: number): string {
  let out = "";
  for (let i = 0; i < length; i++) {
    out = CROCKFORD[Number(value & 31n)] + out;
    value >>= 5n;
  }
  return out;
}

export function newUlid(): string {
  const ms = BigInt(Date.now()) & ((1n << 48n) - 1n);
  const bytes = new Uint8Array(10);
  crypto.getRandomValues(bytes);
  let rand = 0n;
  for (const b of bytes) rand = (rand << 8n) | BigInt(b);
  return encode(ms, 10) + encode(rand, 16);
}

const ULID_RE = /^[0-7][0-9ABCDEFGHJKMNPQRSTVWXYZ]{25}$/;

export function isUlid(value: string): boolean {
  return ULID_RE.test(value);
}
