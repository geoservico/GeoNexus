def classFactory(iface):
    from .geoservico_insumos_plugin import GeoservicoInsumosPlugin
    return GeoservicoInsumosPlugin(iface)
