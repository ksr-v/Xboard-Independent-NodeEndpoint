<?php
namespace Plugin\NodeEndpoint\Services;
use InvalidArgumentException;
final class Certificates
{
    public static function import(string $pem, array $expected): array
    {
        if (!extension_loaded('openssl')) { throw new InvalidArgumentException('PHP 需要 openssl 扩展。'); }
        if (strlen($pem) > 32768 || str_contains($pem, 'PRIVATE KEY')) {
            throw new InvalidArgumentException('只能导入 CA 公共证书，禁止上传私钥。');
        }
        preg_match_all('/-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+\/=\r\n]+-----END CERTIFICATE-----/', $pem, $matches);
        $certs = $matches[0];
        if (!$certs || count($certs) > 2 || count($certs) !== count($expected)
            || trim(str_replace($certs, '', $pem)) !== '') {
            throw new InvalidArgumentException('需要 1 或 2 个 CA 证书，每个对应一个独立可信 SHA-256 指纹。');
        }
        $info = [];
        foreach ($certs as $index => $cert) {
            $parsed = openssl_x509_parse($cert);
            $fingerprint = openssl_x509_fingerprint($cert, 'sha256');
            $wanted = strtolower(str_replace(':', '', trim($expected[$index])));
            if (!$parsed || !preg_match('/^[a-f0-9]{64}$/D', $wanted)
                || !hash_equals($wanted, $fingerprint ?: '')) {
                throw new InvalidArgumentException('CA 指纹不匹配；必须从独立可信渠道核对。');
            }
            if (!str_contains($parsed['extensions']['basicConstraints'] ?? '', 'CA:TRUE')
                || !str_contains($parsed['extensions']['keyUsage'] ?? '', 'Certificate Sign')
                || $parsed['validFrom_time_t'] > time() || $parsed['validTo_time_t'] <= time()) {
                throw new InvalidArgumentException('CA 用途或有效期不正确。');
            }
            $publicKey = openssl_pkey_get_public($cert);
            $details = $publicKey ? openssl_pkey_get_details($publicKey) : false;
            $signature = $parsed['signatureTypeSN'] ?? '';
            $modernSignature = preg_match('/^(?:RSA-SHA(?:256|384|512)|ecdsa-with-SHA(?:256|384|512))$/iD', $signature) === 1;
            $modernKey = is_array($details) && (
                ($details['type'] === OPENSSL_KEYTYPE_RSA && $details['bits'] >= 2048)
                || ($details['type'] === OPENSSL_KEYTYPE_EC
                    && in_array($details['ec']['curve_name'] ?? '', ['prime256v1', 'secp384r1', 'secp521r1'], true))
            );
            if (!$modernSignature || !$modernKey) {
                throw new InvalidArgumentException('CA 必须使用 RSA 2048 位以上或标准 P-256/P-384/P-521 EC 密钥，并使用 SHA-256/384/512 签名。');
            }
            if (!$publicKey || openssl_x509_verify($cert, $publicKey) !== 1) {
                throw new InvalidArgumentException('必须导入有效的自签名私有根 CA。');
            }
            $info[] = self::metadata($parsed, $fingerprint);
        }
        if (count(array_unique(array_column($info, 'fingerprint_sha256'))) !== count($info)) {
            throw new InvalidArgumentException('双信任需要两个不同 CA。');
        }
        return ['pem' => implode("\n", $certs) . "\n", 'certificates' => $info];
    }
    public static function metadata(array $parsed, string $fingerprint): array
    {
        $days = (int) floor(($parsed['validTo_time_t'] - time()) / 86400);
        return ['fingerprint_sha256' => $fingerprint,
            'not_before' => gmdate('c', $parsed['validFrom_time_t']),
            'not_after' => gmdate('c', $parsed['validTo_time_t']),
            'remaining_days' => $days, 'san' => $parsed['extensions']['subjectAltName'] ?? '',
            'issuer' => $parsed['issuer'] ?? [], 'status' => $days < 0 ? 'expired' : 'valid',
            'expiry_alert' => $days <= 30 ? '30 days' : ($days <= 90 ? '90 days' : ($days <= 180 ? '180 days' : null))];
    }
}
