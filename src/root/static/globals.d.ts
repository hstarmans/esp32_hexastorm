declare var Alpine: any;
// needed for to recognize Alpine.js global variable in home.js

/**
 * Base properties and magics injected by Alpine.js into components.
 */
interface AlpineComponent {
    $el: HTMLElement;
    $refs: Record<string, any>;
    $data: Record<string, any>;
    $watch(property: string, callback: (value: any, oldValue?: any) => void): void;
    $nextTick(callback: () => void): void;
    $dispatch(event: string, detail?: any): void;
}

/**
 * Specifically define your printLauncher state and methods
 */
interface PrintLauncher extends AlpineComponent {
    selectedFile: string;
    jobMode: 'laser' | 'cnc';
    laserPower: number | string;
    exposure: number | string;
    posX: number;
    posY: number;
    posZ: number;
    singleFacet: boolean;
    isStarting: boolean;
    homeBeforePrint: boolean;
    useCustomStart: boolean;
    
    init(): void;
    detectMode(filename: string): void;
    startPrint(): Promise<void>;
}