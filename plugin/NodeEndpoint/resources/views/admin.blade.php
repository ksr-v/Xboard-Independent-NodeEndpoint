@php
    $nodeEndpointOriginalHtml = view()->file($__nodeEndpointOriginalPath, $__nodeEndpointOriginalData)->render();
    echo \Plugin\NodeEndpoint\Services\AdminIntegration::inject($nodeEndpointOriginalHtml);
@endphp
