# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`airborne_georef`.

Exposes the raw PIKA-L cube georeferencing functionality as a standalone
QGIS Processing algorithm. Wraps :func:`airborne_georef.write_flat_geotiff`
so users can run raw PIKA-L → georeferenced GeoTIFF from the QGIS toolbox.
"""

import os
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterCrs,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from .src import airborne_georef, models
from . import canvas_styling, qgis_helpers


def _is_empty_output(raw):
    """Return True when the user gave no real OUTPUT value."""
    if raw is None:
        return True
    if isinstance(raw, str) and (not raw or raw == "TEMPORARY_OUTPUT"):
        return True
    return False


class AirborneGeorefAlgorithm(QgsProcessingAlgorithm):
    """Georeference raw PIKA-L flight line cubes to GeoTIFF."""

    BIL = "BIL"
    HDR = "HDR"
    TIMES = "TIMES"
    LCF = "LCF"
    FOV_DEG = "FOV_DEG"
    GROUND_ALT = "GROUND_ALT"
    DEM = "DEM"
    DST_CRS = "DST_CRS"
    RESOLUTION = "RESOLUTION"
    NODATA = "NODATA"
    OUTPUT = "OUTPUT"
    BORESIGHT_ROLL_DEG = "BORESIGHT_ROLL_DEG"
    BORESIGHT_PITCH_DEG = "BORESIGHT_PITCH_DEG"
    BORESIGHT_YAW_DEG = "BORESIGHT_YAW_DEG"
    TIME_OFFSET_S = "TIME_OFFSET_S"
    OUTPUT_FOOTPRINT = "OUTPUT_FOOTPRINT"

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("AirborneGeorefAlgorithm", text)

    def createInstance(self):
        return AirborneGeorefAlgorithm()

    def name(self):
        return "airborne_georef"

    def displayName(self):
        return self.tr("Georeference raw flight line")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Georeferences a raw PIKA-L flight line cube to a GeoTIFF using "
            "navigation data from .lcf and .times sidecar files. Supports "
            "both flat-earth and DEM-aware georeferencing modes.\n\n"
            "Inputs:\n"
            "- Raw cube (.bil/.bip/.bsq)\n"
            "- ENVI header (.hdr) - auto-discovered if omitted\n"
            "- .times file (per-frame timestamps) - auto-discovered if omitted\n"
            "- .lcf file (navigation log) - auto-discovered if omitted\n"
            "- FOV in degrees (required)\n"
            "- Ground altitude (required for flat-earth mode)\n"
            "- Optional DEM raster (triggers DEM-aware mode)\n"
            "- Optional destination CRS (defaults to EPSG:4326)\n\n"
            "Output: georeferenced GeoTIFF with per-pixel coordinates "
            "computed from the navigation data."
        )

    # ---- Parameters --------------------------------------------------------

    def initAlgorithm(self, config=None):
        Double = QgsProcessingParameterNumber.Double
        
        # Raw cube input
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.BIL,
                self.tr("Raw PIKA-L cube (.bil/.bip/.bsq)"),
                optional=False
            )
        )
        
        # Optional sidecar files (auto-discovered if omitted)
        self.addParameter(
            QgsProcessingParameterFile(
                self.HDR,
                self.tr("ENVI header (.hdr) - auto-discovered if omitted"),
                fileType=QgsProcessingParameterFile.File,
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFile(
                self.TIMES,
                self.tr(".times file (per-frame timestamps) - auto-discovered if omitted"),
                fileType=QgsProcessingParameterFile.File,
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFile(
                self.LCF,
                self.tr(".lcf file (navigation log) - auto-discovered if omitted"),
                fileType=QgsProcessingParameterFile.File,
                optional=True
            )
        )
        
        # Required parameters
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FOV_DEG,
                self.tr("Sensor FOV in degrees"),
                type=Double,
                defaultValue=20.0,
                minValue=0.1,
                maxValue=180.0
            )
        )
        
        self.addParameter(
            QgsProcessingParameterNumber(
                self.GROUND_ALT,
                self.tr("Ground altitude (meters, flat-earth mode)"),
                type=Double,
                defaultValue=50.0
            )
        )
        
        # Optional DEM for DEM-aware mode
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DEM,
                self.tr("DEM raster (triggers DEM-aware mode)"),
                optional=True
            )
        )
        
        # Output CRS
        self.addParameter(
            QgsProcessingParameterCrs(
                self.DST_CRS,
                self.tr("Destination CRS"),
                defaultValue="EPSG:4326"
            )
        )
        
        # Optional advanced parameters
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RESOLUTION,
                self.tr("Output resolution (degrees or meters, optional)"),
                type=Double,
                optional=True,
                minValue=0.000001
            )
        )
        
        self.addParameter(
            QgsProcessingParameterNumber(
                self.NODATA,
                self.tr("Nodata value (optional)"),
                type=Double,
                optional=True
            )
        )
        
        # Boresight parameters (advanced)
        boresight_roll_param = QgsProcessingParameterNumber(
            self.BORESIGHT_ROLL_DEG,
            self.tr("Boresight roll (degrees)"),
            type=Double,
            defaultValue=0.0,
            optional=True
        )
        boresight_roll_param.setFlags(boresight_roll_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(boresight_roll_param)
        
        boresight_pitch_param = QgsProcessingParameterNumber(
            self.BORESIGHT_PITCH_DEG,
            self.tr("Boresight pitch (degrees)"),
            type=Double,
            defaultValue=0.0,
            optional=True
        )
        boresight_pitch_param.setFlags(boresight_pitch_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(boresight_pitch_param)
        
        boresight_yaw_param = QgsProcessingParameterNumber(
            self.BORESIGHT_YAW_DEG,
            self.tr("Boresight yaw (degrees)"),
            type=Double,
            defaultValue=0.0,
            optional=True
        )
        boresight_yaw_param.setFlags(boresight_yaw_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(boresight_yaw_param)
        
        time_offset_param = QgsProcessingParameterNumber(
            self.TIME_OFFSET_S,
            self.tr("Time offset (seconds)"),
            type=Double,
            defaultValue=0.0,
            optional=True
        )
        time_offset_param.setFlags(time_offset_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(time_offset_param)
        
        # Optional footprint output
        footprint_param = QgsProcessingParameterVectorDestination(
            self.OUTPUT_FOOTPRINT,
            self.tr("Footprint polygon (optional)"),
            optional=True
        )
        footprint_param.setHelp(self.tr(
            "Optional. If left empty, no footprint is written. "
            "Suggested suffix: <output>.footprint.geojson."
        ))
        self.addParameter(footprint_param)
        
        # Output
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT,
                self.tr("Georeferenced GeoTIFF")
            )
        )

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        # Get input parameters
        bil_layer = self.parameterAsRasterLayer(parameters, self.BIL, context)
        if bil_layer is None:
            raise QgsProcessingException(
                self.invalidRasterError(parameters, self.BIL))
        
        hdr_path = self.parameterAsString(parameters, self.HDR, context)
        times_path = self.parameterAsString(parameters, self.TIMES, context)
        lcf_path = self.parameterAsString(parameters, self.LCF, context)
        
        fov_deg = self.parameterAsDouble(parameters, self.FOV_DEG, context)
        ground_alt = self.parameterAsDouble(parameters, self.GROUND_ALT, context)
        
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        dst_crs = self.parameterAsCrs(parameters, self.DST_CRS, context)
        
        resolution = self.parameterAsDouble(parameters, self.RESOLUTION, context)
        if resolution == 0.0:  # Handle case where user enters 0
            resolution = None
            
        nodata = None
        if parameters.get(self.NODATA) is not None:
            nodata = self.parameterAsDouble(parameters, self.NODATA, context)
        
        # Boresight and time offset parameters
        boresight_roll_deg = self.parameterAsDouble(parameters, self.BORESIGHT_ROLL_DEG, context)
        boresight_pitch_deg = self.parameterAsDouble(parameters, self.BORESIGHT_PITCH_DEG, context)
        boresight_yaw_deg = self.parameterAsDouble(parameters, self.BORESIGHT_YAW_DEG, context)
        time_offset_s = self.parameterAsDouble(parameters, self.TIME_OFFSET_S, context)
        
        # Optional footprint output
        footprint_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_FOOTPRINT, context)
        if not footprint_path:
            footprint_path = None
        
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        
        # Validate inputs
        bil_path = Path(bil_layer.source())
        if not bil_path.exists():
            raise QgsProcessingException(
                self.tr("Raw cube file not found: {}").format(bil_path))
        
        # Create FlightLineMeta - use provided paths or auto-discover
        try:
            if hdr_path or times_path or lcf_path:
                # Use provided paths, fallback to auto-discovery for missing ones
                hdr = Path(hdr_path) if hdr_path else bil_path.with_suffix('.hdr')
                if not hdr.exists():
                    raise QgsProcessingException(
                        self.tr("Required header file not found: {}").format(hdr))
                
                times = Path(times_path) if times_path else bil_path.with_suffix('.times')
                if times_path and not times.exists():
                    raise QgsProcessingException(
                        self.tr("Specified TIMES file not found: {}").format(times))
                elif not times_path and not times.exists():
                    times = None
                
                lcf = Path(lcf_path) if lcf_path else bil_path.with_suffix('.lcf')
                if lcf_path and not lcf.exists():
                    raise QgsProcessingException(
                        self.tr("Specified LCF file not found: {}").format(lcf))
                elif not lcf_path and not lcf.exists():
                    lcf = None
                
                name = bil_path.stem
                meta = models.FlightLineMeta(
                    name=name, bil=bil_path, hdr=hdr, times=times, lcf=lcf)
            else:
                # Auto-discover all sidecar files
                meta = models.discover_flight_line(bil_path)
        except FileNotFoundError as e:
            raise QgsProcessingException(str(e))
        
        # Validate required files exist
        if meta.times is None:
            raise QgsProcessingException(
                self.tr("TIMES file is required but not found"))
        if meta.lcf is None:
            raise QgsProcessingException(
                self.tr("LCF file is required but not found"))
        
        feedback.pushInfo(
            "Flight line: {}".format(meta.name))
        feedback.pushInfo(
            "Raw cube: {}".format(meta.bil))
        feedback.pushInfo(
            "Header: {}".format(meta.hdr))
        feedback.pushInfo(
            "Times: {}".format(meta.times or "None"))
        feedback.pushInfo(
            "LCF: {}".format(meta.lcf or "None"))
        
        # Set DEM path if provided
        dem_path = None
        if dem_layer is not None:
            dem_path = dem_layer.source()
            feedback.pushInfo(
                "Using DEM-aware mode with DEM: {}".format(dem_path))
        else:
            feedback.pushInfo(
                "Using flat-earth mode with ground altitude: {} m".format(ground_alt))
        
        # Convert CRS to string
        dst_crs_str = dst_crs.authid()
        if not dst_crs_str:
            dst_crs_str = "EPSG:4326"
        
        feedback.pushInfo(
            "Output CRS: {}".format(dst_crs_str))
        feedback.pushInfo(
            "Sensor FOV: {}°".format(fov_deg))
        
        if resolution is not None:
            feedback.pushInfo(
                "Output resolution: {}".format(resolution))
        if nodata is not None:
            feedback.pushInfo(
                "Nodata value: {}".format(nodata))
        
        # Set up progress callback
        def progress_callback(fraction, message):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Canceled by user"))
            feedback.setProgress(int(max(0.0, min(1.0, fraction)) * 100))
            if message:
                feedback.pushInfo(message)
        
        # Run georeferencing
        try:
            result = airborne_georef.write_flat_geotiff(
                meta=meta,
                output_path=out_path,
                fov_deg=fov_deg,
                ground_alt=ground_alt,
                dem_path=dem_path,
                dst_crs=dst_crs_str,
                resolution=resolution if resolution is not None else None,
                nodata=nodata,
                boresight_roll_deg=boresight_roll_deg,
                boresight_pitch_deg=boresight_pitch_deg,
                boresight_yaw_deg=boresight_yaw_deg,
                time_offset_s=time_offset_s,
                footprint_path=footprint_path
            )
        except Exception as exc:
            qgis_helpers.handle_processing_exception(exc)
        
        feedback.pushInfo(
            "Georeferenced GeoTIFF written: {}".format(result.output_path))
        feedback.pushInfo(
            "Dimensions: {} x {} x {} bands".format(
                result.width, result.height, result.bands))
        feedback.pushInfo(
            "Output CRS: {}".format(result.crs))
        
        # Attach RGB post-processor for better visualization
        # Try to get band count from the result or default to a reasonable value
        band_count = getattr(result, 'bands', models.DEFAULT_PIKA_L_BANDS)  # PIKA-L typical band count
        self._rgb_post_processor = canvas_styling.attach_rgb_post_processor_if_needed(
            context, out_path, band_count)
        
        return {self.OUTPUT: out_path}