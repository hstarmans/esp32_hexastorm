interface AlpineComponent {
    $el: HTMLElement;
    $refs: Record<string, any>;
    $data: Record<string, any>;
    $watch(property: string, callback: (value: any, oldValue?: any) => void): void;
    $nextTick(callback: () => void): void;
    $dispatch(event: string, detail?: any): void;
}

type AlpineComponentFactory = (this: AlpineComponent & Record<string, any>, ...args: any[]) => Record<string, any>;

interface Alpine {
    store(name: string, value?: any): any;
    data(name: string, callback: AlpineComponentFactory): void;
}

declare var Alpine: Alpine;

interface Window {
    api: {
        post(url: string, payload?: any): Promise<void>;
        gotopoint(position: number[], absolute?: boolean, workspace?: boolean): void;
        clearError(): void;
    };
    diodeTest: () => void;
    saveFacetMeans: () => void;
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